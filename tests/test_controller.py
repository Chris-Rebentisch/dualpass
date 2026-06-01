"""End-to-end tests for the controller against a scaffolded project."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from dualpass import _init, controller
from dualpass.cli import main
from dualpass.memory import lock_path, lock_present, state_dir, units_dir
from dualpass.observability import event_log_path, read_events


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    """Scaffold a fresh dualpass project into tmp_path/proj."""
    target = tmp_path / "proj"
    _init.run_init(target)
    return target


# ── Happy path ────────────────────────────────────────────────────────────────


def test_run_unit_completes_full_chain_with_mock_provider(scaffolded_project: Path) -> None:
    """With --ignore-breakpoints the mock should run all 7 stages cleanly."""
    rc = controller.run_unit(
        "demo-001",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0

    # Every stage produced an artifact and (where applicable) a review.
    udir = units_dir(scaffolded_project, "demo-001")
    expected_artifacts = [
        "research-artifact-v1.md",
        "outline-artifact-v1.md",
        "spec-artifact-v1.md",
        "prompt-artifact-v1.md",
        "code-artifact-v1.md",
        "audit-artifact-v1.md",
        "handoff-artifact-v1.md",
    ]
    for name in expected_artifacts:
        assert (udir / name).is_file(), f"missing artifact {name}"

    # Code stage has no reviewer (its review surface is the audit stage), so
    # no code-review-v1.md should be produced.
    assert not (udir / "code-review-v1.md").is_file()
    # Single-pass stages produce one review file with no suffix.
    for stage in ("research", "outline", "audit", "handoff"):
        assert (udir / f"{stage}-review-v1.md").is_file(), f"missing review {stage}"
    # spec + prompt are dual-pass in the bundled example — two parallel reviewer artifacts each.
    for stage in ("spec", "prompt"):
        assert (udir / f"{stage}-review-v1-a.md").is_file(), f"missing {stage} review a"
        assert (udir / f"{stage}-review-v1-b.md").is_file(), f"missing {stage} review b"
        assert not (udir / f"{stage}-review-v1.md").is_file()


def test_run_unit_emits_structured_events(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-002",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    events = read_events("demo-002", scaffolded_project)
    types = [e.type for e in events]
    assert "unit_started" in types
    assert "unit_completed" in types
    assert types.count("stage_completed") == 7
    assert "lockfile_acquired" in types
    assert "lockfile_released" in types


def test_run_unit_releases_lock_after_completion(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-003",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert not lock_present("demo-003", scaffolded_project)


# ── Breakpoint behavior ───────────────────────────────────────────────────────


def test_run_unit_respects_code_breakpoint_in_example_project(scaffolded_project: Path) -> None:
    """The bundled example sets breakpoints.code: true. Default run should pause."""
    out = io.StringIO()
    with redirect_stdout(out):
        rc = controller.run_unit("demo-bp", provider="mock", project_root=scaffolded_project)
    assert rc == 0
    assert "paused before stage 'code'" in out.getvalue()

    # Stages BEFORE code ran; code and later did not.
    udir = units_dir(scaffolded_project, "demo-bp")
    assert (udir / "prompt-artifact-v1.md").is_file()
    assert not (udir / "code-artifact-v1.md").is_file()


def test_run_unit_ignore_breakpoints_skips_pause(scaffolded_project: Path) -> None:
    rc = controller.run_unit(
        "demo-bp-ignore",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    udir = units_dir(scaffolded_project, "demo-bp-ignore")
    assert (udir / "code-artifact-v1.md").is_file()
    assert (udir / "handoff-artifact-v1.md").is_file()


def test_run_unit_resumes_from_stage(scaffolded_project: Path) -> None:
    """--from-stage outline should skip research."""
    rc = controller.run_unit(
        "demo-resume",
        provider="mock",
        from_stage="outline",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    udir = units_dir(scaffolded_project, "demo-resume")
    assert not (udir / "research-artifact-v1.md").is_file()
    assert (udir / "outline-artifact-v1.md").is_file()
    assert (udir / "handoff-artifact-v1.md").is_file()


def test_run_unit_unknown_from_stage_returns_two(scaffolded_project: Path) -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        rc = controller.run_unit(
            "demo-bad-stage",
            provider="mock",
            from_stage="does-not-exist",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    assert rc == 2
    assert "unknown --from-stage" in err.getvalue()


# ── Lockfile ──────────────────────────────────────────────────────────────────


def test_run_unit_refuses_when_lock_present(scaffolded_project: Path) -> None:
    # Pre-create the lockfile to simulate another in-flight run.
    state_dir(scaffolded_project)
    lock = lock_path("demo-locked", scaffolded_project)
    lock.write_text(json.dumps({"unit": "demo-locked", "pid": 99999}))

    err = io.StringIO()
    with redirect_stderr(err):
        rc = controller.run_unit(
            "demo-locked",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    assert rc == 2
    assert "lock already held" in err.getvalue()
    # We did NOT release the foreign lock.
    assert lock.is_file()


# ── Rejection / max-rounds-exhausted ─────────────────────────────────────────


def test_run_unit_halts_when_max_rounds_exhausted(scaffolded_project: Path) -> None:
    """If the mock rejects every round, the controller should halt at that stage."""
    # Patch the provider factory just for this test by injecting a scripted mock.
    from dualpass import providers
    from dualpass.providers.mock import MockProvider, MockScript

    original = providers.get_provider

    def scripted(name: str, *, agents_config=None):
        if name == "mock":
            return MockProvider(scripts={"research": MockScript(review_verdicts=["rejected"])})
        return original(name, agents_config=agents_config)

    providers.get_provider = scripted  # type: ignore[assignment]
    try:
        err = io.StringIO()
        with redirect_stderr(err):
            rc = controller.run_unit(
                "demo-stuck",
                provider="mock",
                project_root=scaffolded_project,
                ignore_breakpoints=True,
            )
    finally:
        providers.get_provider = original  # type: ignore[assignment]

    assert rc == 1
    assert "blocked" in err.getvalue()
    # Subsequent stages should not have run.
    udir = units_dir(scaffolded_project, "demo-stuck")
    assert not (udir / "outline-artifact-v1.md").is_file()


# ── CLI surface ──────────────────────────────────────────────────────────────


def test_cli_run_executes_full_chain(scaffolded_project: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(
            [
                "run",
                "--unit",
                "demo-cli",
                "--provider",
                "mock",
                "--ignore-breakpoints",
                "--project",
                str(scaffolded_project),
            ]
        )
    assert rc == 0
    assert "completed 7 stage(s)" in out.getvalue()


def test_cli_run_pauses_at_breakpoint_by_default(scaffolded_project: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(
            [
                "run",
                "--unit",
                "demo-cli-bp",
                "--provider",
                "mock",
                "--project",
                str(scaffolded_project),
            ]
        )
    assert rc == 0
    assert "paused before stage 'code'" in out.getvalue()


def test_run_unit_works_with_live_provider_against_fake_clis(scaffolded_project: Path) -> None:
    """End-to-end: rewrite agents.yaml to point at fake shell scripts and run live.

    Exercises the same path real `claude` / `cursor-agent` calls would take —
    just with deterministic stdout instead of a network roundtrip.
    """
    import stat
    import textwrap

    # Drop two tiny shell scripts the harness will shell out to.
    bin_dir = scaffolded_project / "bin"
    bin_dir.mkdir()
    author_path = bin_dir / "fake-author.sh"
    author_path.write_text("#!/bin/sh\nprintf '# stage output\n\nbody from author\n'\n")
    author_path.chmod(author_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    reviewer_path = bin_dir / "fake-reviewer.sh"
    reviewer_path.write_text("#!/bin/sh\nprintf 'Looks fine.\n\nVerdict: approved\n'\n")
    reviewer_path.chmod(reviewer_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Point agents.yaml at the scripts.
    agents_yaml = scaffolded_project / "config" / "agents.yaml"
    agents_yaml.write_text(
        textwrap.dedent(f"""\
            roles:
              author:
                command: "{author_path} {{prompt}}"
                timeout_seconds: 15
              reviewer:
                command: "{reviewer_path} {{prompt}}"
                timeout_seconds: 15
        """)
    )

    rc = controller.run_unit(
        "demo-live",
        provider="live",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    udir = units_dir(scaffolded_project, "demo-live")
    # The author's stdout should land in the artifact, with the dualpass header.
    body = (udir / "research-artifact-v1.md").read_text()
    assert "body from author" in body
    assert "dualpass-served-by: author" in body
    # The reviewer's stdout should land in the review.
    review = (udir / "research-review-v1.md").read_text()
    assert "Verdict: approved" in review


def test_event_log_file_is_jsonl(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-jsonl",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    path = event_log_path("demo-jsonl", scaffolded_project)
    assert path.is_file()
    lines = path.read_text(encoding="utf-8").splitlines()
    # Every line must be parseable JSON.
    for line in lines:
        json.loads(line)
