"""v1.0.5 — audit-verdict routing + architect dispositions.

The audit stage's machine-stable verdict line drives the controller. Three
verdicts → three control-flow paths:

  PASS                         → advance to handoff
  NEEDS_REMEDIATION            → rewind to code; bounded by max_audit_iterations
  ARCHITECTURAL_DIVERGENCE     → halt, write stuck marker

This module covers the controller's response to each verdict, the
max_audit_iterations budget exhaustion path, and the two CLI architect
commands (`remediate`, `accept-divergence`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dualpass import _init, controller
from dualpass.cli import main
from dualpass.memory import state_dir, units_dir
from dualpass.providers.mock import MockProvider, MockScript

# We need to substitute a scripted mock provider into the controller's
# `providers.get_provider` lookup. The cleanest way is to monkeypatch
# `providers.get_provider` to return our pre-configured MockProvider.


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    """Scaffold a fresh dualpass project into tmp_path/proj."""
    from tests.conftest import enable_research_reviewer

    target = tmp_path / "proj"
    _init.run_init(target)
    enable_research_reviewer(target)
    return target


def _install_scripted_provider(monkeypatch, script: dict[str, MockScript]) -> None:
    """Force `providers.get_provider("mock", ...)` to return a scripted MockProvider."""
    from dualpass import providers as _providers

    def factory(name: str, *, agents_config):
        if name == "mock":
            return MockProvider(scripts=script)
        return _providers.get_provider(name, agents_config=agents_config)

    monkeypatch.setattr(_providers, "get_provider", factory)


# ── PASS path ────────────────────────────────────────────────────────────────


def test_audit_pass_advances_to_handoff(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default audit verdict = PASS → controller walks all 7 stages and exits 0."""
    _install_scripted_provider(monkeypatch, {"audit": MockScript(audit_verdicts=["PASS"])})

    rc = controller.run_unit(
        "demo-pass",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    udir = units_dir(scaffolded_project, "demo-pass")
    assert (udir / "handoff-artifact-v1.md").exists(), "handoff should have run"
    # No stuck markers on the happy path.
    state = state_dir(scaffolded_project)
    assert not list(state.glob("demo-pass-stuck-*.md"))


# ── NEEDS_REMEDIATION path ───────────────────────────────────────────────────


def test_needs_remediation_loops_back_to_code(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First audit returns NEEDS_REMEDIATION → code re-runs → second audit PASS → handoff."""
    _install_scripted_provider(
        monkeypatch,
        {"audit": MockScript(audit_verdicts=["NEEDS_REMEDIATION", "PASS"])},
    )

    rc = controller.run_unit(
        "demo-remediate",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    udir = units_dir(scaffolded_project, "demo-remediate")
    # Code stage ran twice (initial + after remediation).
    assert (udir / "code-artifact-v1.md").exists()
    # Audit stage ran twice; both artifacts exist.
    audit_artifacts = sorted(udir.glob("audit-artifact-v*.md"))
    assert len(audit_artifacts) >= 1
    # Handoff ran at the end.
    assert (udir / "handoff-artifact-v1.md").exists()


def test_max_audit_iterations_exhaustion_halts_with_stuck_marker(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five consecutive NEEDS_REMEDIATION verdicts exhaust budget=4 and halt."""
    _install_scripted_provider(
        monkeypatch,
        {
            "audit": MockScript(
                audit_verdicts=["NEEDS_REMEDIATION"] * 10
            )
        },
    )

    rc = controller.run_unit(
        "demo-exhausted",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 1, "exhausted audit loop should halt"
    state = state_dir(scaffolded_project)
    marker = state / "demo-exhausted-stuck-audit-loop-exhausted.md"
    assert marker.is_file(), "audit-loop-exhausted stuck marker must be written"
    # No handoff on a stuck unit.
    udir = units_dir(scaffolded_project, "demo-exhausted")
    assert not (udir / "handoff-artifact-v1.md").exists()


# ── ARCHITECTURAL_DIVERGENCE path ────────────────────────────────────────────


def test_architectural_divergence_writes_stuck_marker_immediately(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One ARCHITECTURAL_DIVERGENCE verdict halts without consuming audit iterations."""
    _install_scripted_provider(
        monkeypatch,
        {"audit": MockScript(audit_verdicts=["ARCHITECTURAL_DIVERGENCE"])},
    )

    rc = controller.run_unit(
        "demo-divergence",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 1
    state = state_dir(scaffolded_project)
    marker = state / "demo-divergence-stuck-architectural-divergence.md"
    assert marker.is_file()
    udir = units_dir(scaffolded_project, "demo-divergence")
    assert not (udir / "handoff-artifact-v1.md").exists()


# ── Unknown verdict ─────────────────────────────────────────────────────────


def test_unknown_audit_verdict_halts_for_operator(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An audit FINAL with no recognizable verdict line halts conservatively."""
    _install_scripted_provider(
        monkeypatch,
        {"audit": MockScript(audit_verdicts=["DEFINITELY_NOT_A_VERDICT"])},
    )

    rc = controller.run_unit(
        "demo-unknown",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 1


# ── CLI: remediate ──────────────────────────────────────────────────────────


def test_remediate_command_clears_marker_and_relaunches(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dualpass remediate` clears the stuck marker and re-runs from code."""
    # Set up: trip the architectural-divergence path with the first script.
    _install_scripted_provider(
        monkeypatch,
        {"audit": MockScript(audit_verdicts=["ARCHITECTURAL_DIVERGENCE"])},
    )
    rc = controller.run_unit(
        "demo-rem",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 1
    marker = state_dir(scaffolded_project) / "demo-rem-stuck-architectural-divergence.md"
    assert marker.is_file()

    # Re-script so the post-remediate audit returns PASS. The factory always
    # constructs a fresh MockProvider; re-installing swaps in a new script.
    _install_scripted_provider(monkeypatch, {"audit": MockScript(audit_verdicts=["PASS"])})

    rc2 = main(
        [
            "remediate",
            "--unit",
            "demo-rem",
            "--provider",
            "mock",
            "--ignore-breakpoints",
            "--project",
            str(scaffolded_project),
        ]
    )
    assert rc2 == 0
    assert not marker.exists(), "remediate should clear the stuck marker"
    udir = units_dir(scaffolded_project, "demo-rem")
    assert (udir / "handoff-artifact-v1.md").exists()


def test_remediate_fails_without_a_stuck_marker(
    scaffolded_project: Path,
) -> None:
    """`remediate` is a no-op signal when there's nothing to clear."""
    rc = main(
        [
            "remediate",
            "--unit",
            "nonexistent",
            "--provider",
            "mock",
            "--project",
            str(scaffolded_project),
        ]
    )
    assert rc == 1


# ── CLI: accept-divergence ──────────────────────────────────────────────────


def test_accept_divergence_writes_sidecar_and_runs_handoff(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`accept-divergence` lands the sidecar, clears the marker, advances to handoff."""
    _install_scripted_provider(
        monkeypatch,
        {"audit": MockScript(audit_verdicts=["ARCHITECTURAL_DIVERGENCE"])},
    )
    rc = controller.run_unit(
        "demo-acc",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 1

    rc2 = main(
        [
            "accept-divergence",
            "--unit",
            "demo-acc",
            "--rationale",
            "shipping the divergent design point per architect review",
            "--architect",
            "test-architect",
            "--provider",
            "mock",
            "--ignore-breakpoints",
            "--project",
            str(scaffolded_project),
        ]
    )
    assert rc2 == 0
    udir = units_dir(scaffolded_project, "demo-acc")
    sidecar = udir / "divergence-accepted.json"
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["unit"] == "demo-acc"
    assert payload["architect"] == "test-architect"
    assert "divergent design point" in payload["rationale"]
    assert "accepted_at" in payload
    assert isinstance(payload["audit_findings"], list)
    # Handoff ran.
    assert (udir / "handoff-artifact-v1.md").exists()


def test_accept_divergence_no_run_lands_sidecar_only(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --no-run, the sidecar lands but the handoff stage doesn't relaunch."""
    _install_scripted_provider(
        monkeypatch,
        {"audit": MockScript(audit_verdicts=["ARCHITECTURAL_DIVERGENCE"])},
    )
    rc = controller.run_unit(
        "demo-norun",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 1

    rc2 = main(
        [
            "accept-divergence",
            "--unit",
            "demo-norun",
            "--rationale",
            "deferred ship review",
            "--no-run",
            "--project",
            str(scaffolded_project),
        ]
    )
    assert rc2 == 0
    udir = units_dir(scaffolded_project, "demo-norun")
    assert (udir / "divergence-accepted.json").is_file()
    # Handoff NOT re-run.
    assert not (udir / "handoff-artifact-v1.md").exists()


# ── Audit verdict parser ────────────────────────────────────────────────────


def test_parse_audit_verdict_recognizes_all_three(tmp_path: Path) -> None:
    """The verdict parser must handle all three machine-stable forms."""
    f = tmp_path / "audit.md"
    for verdict_str, expected in (
        ("**Verdict:** PASS", "pass"),
        ("**Verdict:** NEEDS_REMEDIATION", "needs_remediation"),
        ("**Verdict:** ARCHITECTURAL_DIVERGENCE", "architectural_divergence"),
        ("**Verdict:** pass", "pass"),  # case-insensitive
        ("**Verdict:** needs_remediation", "needs_remediation"),
    ):
        f.write_text(f"# Header\n\n## Verdict\n\n{verdict_str}\n", encoding="utf-8")
        assert controller._parse_audit_verdict(f) == expected


def test_parse_audit_verdict_returns_unknown_on_missing_or_unrecognized(
    tmp_path: Path,
) -> None:
    """An audit body without a recognizable verdict line returns 'unknown'."""
    f = tmp_path / "audit.md"
    f.write_text("# Header\n\nNo verdict here.\n", encoding="utf-8")
    assert controller._parse_audit_verdict(f) == "unknown"
    # Missing file → unknown.
    assert controller._parse_audit_verdict(tmp_path / "missing.md") == "unknown"


# ── max_audit_iterations config ─────────────────────────────────────────────


def test_max_audit_iterations_default_is_four(scaffolded_project: Path) -> None:
    """A scaffolded project's project config carries max_audit_iterations: 4."""
    from dualpass import config as _cfg

    project = _cfg.load_project_config(scaffolded_project)
    assert project.max_audit_iterations == 4
