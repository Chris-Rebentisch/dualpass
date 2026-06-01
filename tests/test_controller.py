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
    from tests.conftest import enable_research_reviewer
    target = tmp_path / "proj"
    _init.run_init(target)
    enable_research_reviewer(target)
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

    # Drop two tiny shell scripts the harness will shell out to. The author
    # emits valid YAML frontmatter so the bundled example's `check-frontmatter`
    # preflight gate passes; the reviewer just returns an approval verdict.
    bin_dir = scaffolded_project / "bin"
    bin_dir.mkdir()
    author_path = bin_dir / "fake-author.sh"
    author_path.write_text(
        "#!/bin/sh\n"
        "printf -- '---\\ntitle: fake stage output\\n---\\n\\n"
        "# stage output\\n\\nbody from author\\n'\n"
    )
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
    # The author's stdout should land in the artifact body (clean — v1.0.4
    # moved diagnostic info to a .meta.json sidecar).
    body = (udir / "research-artifact-v1.md").read_text()
    assert "body from author" in body
    assert "dualpass-served-by" not in body
    sidecar = json.loads((udir / "research-artifact-v1.meta.json").read_text())
    assert sidecar["served_by"] == "author"
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


# ── Phase 1 wiring: context bundles ──────────────────────────────────────────


def test_stage_start_invokes_build_stage_context(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each stage round should build its context bundle before invoking the author."""
    calls: list[tuple[str, str]] = []

    def fake_build_stage_context(*, unit_id: str, stage: str, project_root: Path, predecessor_stage):
        calls.append(("ctx", stage))
        return scaffolded_project / ".dualpass-state" / f"{unit_id}-stage-context.md"

    def fake_build_precedent_cache(*, unit_id: str, stage: str, project_root: Path, peer_count: int = 3):
        calls.append(("precedent", stage))
        return scaffolded_project / ".dualpass-state" / f"{unit_id}-precedent-cache.md"

    monkeypatch.setattr(controller, "build_stage_context", fake_build_stage_context)
    monkeypatch.setattr(controller, "build_precedent_cache", fake_build_precedent_cache)

    rc = controller.run_unit(
        "demo-ctx",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0

    ctx_stages = [s for kind, s in calls if kind == "ctx"]
    # Seven stages × at least one round each.
    assert set(ctx_stages) == {
        "research",
        "outline",
        "spec",
        "prompt",
        "code",
        "audit",
        "handoff",
    }

    # Precedent cache only runs for outline / spec / prompt.
    precedent_stages = [s for kind, s in calls if kind == "precedent"]
    assert set(precedent_stages) == {"outline", "spec", "prompt"}


def test_stage_start_proceeds_when_context_builder_raises(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing context bundle is logged but never halts the run."""

    def boom(**_kwargs):
        raise RuntimeError("context bundler intentionally exploded")

    monkeypatch.setattr(controller, "build_stage_context", boom)
    monkeypatch.setattr(controller, "build_precedent_cache", boom)

    rc = controller.run_unit(
        "demo-ctx-boom",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    udir = units_dir(scaffolded_project, "demo-ctx-boom")
    assert (udir / "handoff-artifact-v1.md").is_file()


# ── Phase 1 wiring: build-complete marker exit_signal ────────────────────────


def _install_marker_aware_mock(markers: dict, scripts: dict | None = None):
    """Swap the global provider factory for a MockProvider with a marker override."""
    from dualpass import providers as _providers
    from dualpass.providers.mock import MockProvider

    original = _providers.get_provider

    def factory(name: str, *, agents_config=None):
        if name == "mock":
            return MockProvider(scripts=scripts, markers=markers)
        return original(name, agents_config=agents_config)

    _providers.get_provider = factory  # type: ignore[assignment]
    return original


def _restore_provider_factory(original) -> None:
    from dualpass import providers as _providers

    _providers.get_provider = original  # type: ignore[assignment]


def test_author_stop_marker_halts_with_stuck_marker(scaffolded_project: Path) -> None:
    """exit_signal: stop should halt the run and drop the author-stop stuck marker."""
    from dualpass.providers.mock import MockMarker

    original = _install_marker_aware_mock(
        markers={
            "research": MockMarker(
                exit_signal="stop",
                status="blocked",
                blocker_kind="spec_defect",
                reason="needs operator review before continuing",
            )
        }
    )
    try:
        rc = controller.run_unit(
            "demo-stop",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    finally:
        _restore_provider_factory(original)

    assert rc == 1
    stuck = state_dir(scaffolded_project) / "demo-stop-stuck-author-stop.md"
    assert stuck.is_file()
    body = stuck.read_text()
    assert "needs operator review before continuing" in body

    # stage_blocked event was emitted and `escalated` is NOT set.
    events = read_events("demo-stop", scaffolded_project)
    blocked = next(e for e in events if e.type == "stage_blocked")
    assert blocked.payload.get("exit_signal") == "stop"
    assert "escalated" not in blocked.payload

    # Subsequent stages did not run.
    udir = units_dir(scaffolded_project, "demo-stop")
    assert not (udir / "outline-artifact-v1.md").is_file()


def test_author_escalate_marker_uses_distinct_filename_and_event_flag(
    scaffolded_project: Path,
) -> None:
    """exit_signal: escalate should use a different stuck-marker filename + escalated=true event."""
    from dualpass.providers.mock import MockMarker

    original = _install_marker_aware_mock(
        markers={
            "research": MockMarker(
                exit_signal="escalate",
                status="blocked",
                reason="design contradiction — escalating to architect",
            )
        }
    )
    try:
        rc = controller.run_unit(
            "demo-escalate",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    finally:
        _restore_provider_factory(original)

    assert rc == 1
    escalate_marker = state_dir(scaffolded_project) / "demo-escalate-stuck-author-escalate.md"
    stop_marker = state_dir(scaffolded_project) / "demo-escalate-stuck-author-stop.md"
    assert escalate_marker.is_file()
    assert not stop_marker.is_file()

    events = read_events("demo-escalate", scaffolded_project)
    blocked = next(e for e in events if e.type == "stage_blocked")
    assert blocked.payload.get("escalated") is True
    assert blocked.payload.get("exit_signal") == "escalate"


def test_author_continue_marker_proceeds_to_reviewer(scaffolded_project: Path) -> None:
    """exit_signal: continue (the default mock marker) leaves the loop unchanged."""
    rc = controller.run_unit(
        "demo-continue",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    # All stages produced a reviewer artifact except code (no reviewer configured).
    udir = units_dir(scaffolded_project, "demo-continue")
    assert (udir / "research-review-v1.md").is_file()
    assert (udir / "handoff-review-v1.md").is_file()

    # No stuck markers from the continue-signal path.
    sdir = state_dir(scaffolded_project)
    assert not list(sdir.glob("demo-continue-stuck-*.md"))


def test_malformed_marker_does_not_halt_pipeline(scaffolded_project: Path) -> None:
    """A corrupt build-complete marker is logged and ignored; the run completes."""
    from dualpass import providers as _providers
    from dualpass.providers.mock import MockProvider

    original = _providers.get_provider

    class MalformedMarkerMock(MockProvider):
        def invoke_author(self, ctx):
            result = super().invoke_author(ctx)
            # Overwrite the marker the parent wrote with junk so read_build_marker raises.
            (ctx.project_root / ".dualpass-state" / f"{ctx.unit_id}-build-complete.md").write_text(
                "this is not yaml frontmatter — no fences at all\n",
                encoding="utf-8",
            )
            return result

    def factory(name: str, *, agents_config=None):
        if name == "mock":
            return MalformedMarkerMock()
        return original(name, agents_config=agents_config)

    _providers.get_provider = factory  # type: ignore[assignment]
    try:
        rc = controller.run_unit(
            "demo-bad-marker",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    finally:
        _providers.get_provider = original  # type: ignore[assignment]

    assert rc == 0
    udir = units_dir(scaffolded_project, "demo-bad-marker")
    assert (udir / "handoff-artifact-v1.md").is_file()
    # No stuck markers — malformed should fall back to "continue" semantics.
    sdir = state_dir(scaffolded_project)
    assert not list(sdir.glob("demo-bad-marker-stuck-*.md"))


# ── Phase 1 wiring: preflight gates ──────────────────────────────────────────


def test_preflight_gates_pass_when_artifact_satisfies_them(scaffolded_project: Path) -> None:
    """The mock writes frontmatter; the research stage's check-frontmatter gate passes."""
    rc = controller.run_unit(
        "demo-gates-ok",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    events = read_events("demo-gates-ok", scaffolded_project)
    # No gate failures should have fired.
    assert not any(e.type == "gate_failed" for e in events)
    # Every stage with a reviewer produced a review artifact.
    udir = units_dir(scaffolded_project, "demo-gates-ok")
    assert (udir / "research-review-v1.md").is_file()


def test_preflight_gate_failure_writes_feedback_and_skips_reviewer(
    scaffolded_project: Path,
) -> None:
    """A failing gate must emit gate_failed, write a feedback sidecar, and NOT call reviewer."""
    from dualpass import providers as _providers
    from dualpass.providers.mock import MockProvider

    reviewer_calls: list[str] = []

    original = _providers.get_provider

    class NoFrontmatterMock(MockProvider):
        """Mock whose author writes an artifact WITHOUT frontmatter.

        That guarantees `check-frontmatter` rejects each round and lets us
        verify the gate-failure path without monkey-patching the gate itself.
        """

        def invoke_author(self, ctx):
            # Write a frontmatter-less artifact so check-frontmatter rejects.
            artifact = ctx.units_dir / f"{ctx.stage.name}-artifact-v{ctx.round_number}.md"
            artifact.write_text(
                f"# {ctx.stage.name} round {ctx.round_number}\n\nbody but no frontmatter\n",
                encoding="utf-8",
            )
            # Still write a continue marker so the marker gate (used by code stage) is happy.
            self._write_marker(ctx)
            from dualpass.providers.base import AuthorResult

            return AuthorResult(artifact_path=artifact, served_by="no-fm-mock")

        def invoke_reviewer(self, ctx, artifact):
            reviewer_calls.append(ctx.stage.name)
            return super().invoke_reviewer(ctx, artifact)

    def factory(name: str, *, agents_config=None):
        if name == "mock":
            return NoFrontmatterMock()
        return original(name, agents_config=agents_config)

    _providers.get_provider = factory  # type: ignore[assignment]
    try:
        rc = controller.run_unit(
            "demo-gate-fail",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    finally:
        _providers.get_provider = original  # type: ignore[assignment]

    # The research stage has check-frontmatter and our mock always rejects → halts.
    assert rc == 1

    events = read_events("demo-gate-fail", scaffolded_project)
    gate_events = [e for e in events if e.type == "gate_failed"]
    assert gate_events, "expected at least one gate_failed event"
    # Reviewer was NEVER called for the research stage.
    assert "research" not in reviewer_calls

    # Feedback sidecar exists for the research stage's first round.
    udir = units_dir(scaffolded_project, "demo-gate-fail")
    feedback = udir / "research-gate-feedback-v1.md"
    assert feedback.is_file()
    body = feedback.read_text()
    assert "frontmatter" in body.lower()


# ── Phase 1 wiring: config validates gate names ──────────────────────────────


def test_config_validate_reports_unknown_gate_name(scaffolded_project: Path) -> None:
    """Stages referencing an unregistered gate name should produce a validation error."""
    stages_path = scaffolded_project / "config" / "stages.yaml"
    stages_path.write_text(
        "stages:\n"
        "  - name: research\n"
        "    author_skill: skills/research/SKILL.md\n"
        "    preflight_gates:\n"
        "      - check-frontmatter\n"
        "      - check-totally-made-up-gate\n"
    )

    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["config", "validate", "--project", str(scaffolded_project)])
    assert rc == 1
    msg = err.getvalue()
    assert "check-totally-made-up-gate" in msg
    assert "unknown gate" in msg


# ── Phase 1 wiring: auto_lock_finals ─────────────────────────────────────────


def _set_auto_lock_finals(project_root: Path, value: bool) -> None:
    """Toggle auto_lock_finals in the scaffolded project's dualpass.json."""
    dp_path = project_root / "config" / "dualpass.json"
    data = json.loads(dp_path.read_text())
    data["auto_lock_finals"] = value
    dp_path.write_text(json.dumps(data, indent=2))


def test_auto_lock_finals_true_emits_final_copy(scaffolded_project: Path) -> None:
    """When auto_lock_finals: true, every approved stage gets a <stage>-vN-FINAL.md copy."""
    _set_auto_lock_finals(scaffolded_project, True)
    rc = controller.run_unit(
        "demo-lock",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0

    udir = units_dir(scaffolded_project, "demo-lock")
    # Single-pass stages produce one FINAL per stage.
    for stage in ("research", "outline", "spec", "prompt", "code", "audit", "handoff"):
        assert (udir / f"{stage}-v1-FINAL.md").is_file(), f"expected FINAL for {stage}"

    # Each FINAL is a verbatim copy of its source artifact.
    src = (udir / "research-artifact-v1.md").read_text()
    final = (udir / "research-v1-FINAL.md").read_text()
    assert src == final

    # stage_finalized events landed for every stage.
    events = read_events("demo-lock", scaffolded_project)
    finalized_stages = [e.stage for e in events if e.type == "stage_finalized"]
    assert set(finalized_stages) == {
        "research",
        "outline",
        "spec",
        "prompt",
        "code",
        "audit",
        "handoff",
    }


def test_auto_lock_finals_false_creates_no_final_copy(scaffolded_project: Path) -> None:
    """When auto_lock_finals: false, no -FINAL.md files are emitted."""
    _set_auto_lock_finals(scaffolded_project, False)
    rc = controller.run_unit(
        "demo-no-lock",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0

    udir = units_dir(scaffolded_project, "demo-no-lock")
    finals = list(udir.glob("*-FINAL.md"))
    assert finals == []

    events = read_events("demo-no-lock", scaffolded_project)
    assert not any(e.type == "stage_finalized" for e in events)


def test_auto_lock_finals_with_dual_pass_emits_exactly_one_final(scaffolded_project: Path) -> None:
    """Dual-pass approval + auto_lock_finals must produce ONE FINAL copy, not two."""
    _set_auto_lock_finals(scaffolded_project, True)
    rc = controller.run_unit(
        "demo-lock-dp",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0

    udir = units_dir(scaffolded_project, "demo-lock-dp")
    # spec is the dual-pass stage in the bundled example.
    spec_finals = sorted(udir.glob("spec-v*-FINAL.md"))
    assert len(spec_finals) == 1, f"expected exactly one spec FINAL, got {spec_finals}"
    assert spec_finals[0].name == "spec-v1-FINAL.md"

    # Same for prompt (also dual-pass in the bundled example).
    prompt_finals = sorted(udir.glob("prompt-v*-FINAL.md"))
    assert len(prompt_finals) == 1

    # Exactly one stage_finalized event per dual-pass stage too.
    events = read_events("demo-lock-dp", scaffolded_project)
    spec_finalized = [e for e in events if e.type == "stage_finalized" and e.stage == "spec"]
    assert len(spec_finalized) == 1
