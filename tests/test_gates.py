"""Tests for the preflight gate registry and built-in gate implementations."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dualpass import gates
from dualpass.gates import (
    GateContext,
    GateResult,
    get_gate,
    list_gates,
    register_gate,
    run_gates,
)
from dualpass.memory import BuildMarker, lock_path


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """An empty project root with the state directory pre-created."""
    (tmp_path / ".dualpass-state").mkdir()
    return tmp_path


def _write_artifact(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _ctx(
    project_root: Path,
    artifact_path: Path,
    *,
    unit_id: str = "unit-01",
    stage: str = "spec",
    config: dict | None = None,
) -> GateContext:
    return GateContext(
        unit_id=unit_id,
        stage=stage,
        project_root=project_root,
        artifact_path=artifact_path,
        config=config,
    )


# ── Registry round-trip ───────────────────────────────────────────────────────


def test_registry_round_trip() -> None:
    """register_gate / get_gate / list_gates form a coherent round-trip."""

    def my_gate(ctx: GateContext) -> GateResult:
        return GateResult(passed=True, diagnostic="ok")

    register_gate("test-gate-x", my_gate)
    try:
        assert get_gate("test-gate-x") is my_gate
        assert "test-gate-x" in list_gates()
    finally:
        # Don't pollute the registry across tests.
        gates._REGISTRY.pop("test-gate-x", None)


def test_unknown_gate_returns_synthetic_failure(project_root: Path) -> None:
    """An unregistered gate name yields a synthetic failure, not an exception."""
    artifact = project_root / "artifact.md"
    _write_artifact(artifact, "body")
    results = run_gates(["definitely-not-a-real-gate"], _ctx(project_root, artifact))
    assert len(results) == 1
    assert results[0].passed is False
    assert "not registered" in results[0].diagnostic
    assert "Available" in results[0].diagnostic


def test_multiple_gates_run_in_order_no_short_circuit(project_root: Path) -> None:
    """All declared gates run, in order, even when an earlier one fails."""
    calls: list[str] = []

    def fail_gate(ctx: GateContext) -> GateResult:
        calls.append("fail")
        return GateResult(passed=False, diagnostic="nope")

    def pass_gate(ctx: GateContext) -> GateResult:
        calls.append("pass")
        return GateResult(passed=True, diagnostic="ok")

    register_gate("test-fail", fail_gate)
    register_gate("test-pass", pass_gate)
    try:
        artifact = project_root / "artifact.md"
        _write_artifact(artifact, "body")
        results = run_gates(["test-fail", "test-pass"], _ctx(project_root, artifact))
        assert [r.passed for r in results] == [False, True]
        assert calls == ["fail", "pass"]
    finally:
        gates._REGISTRY.pop("test-fail", None)
        gates._REGISTRY.pop("test-pass", None)


# ── check-frontmatter ─────────────────────────────────────────────────────────


def test_check_frontmatter_passes_with_required_fields(project_root: Path) -> None:
    artifact = project_root / "a.md"
    _write_artifact(
        artifact,
        "---\ntitle: Hello\nversion: 1\n---\n\nbody here\n",
    )
    gate = get_gate("check-frontmatter")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, config={"required_fields": ["title", "version"]}))
    assert result.passed is True


def test_check_frontmatter_fails_on_missing_field(project_root: Path) -> None:
    artifact = project_root / "a.md"
    _write_artifact(artifact, "---\ntitle: Hello\n---\n\nbody\n")
    gate = get_gate("check-frontmatter")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, config={"required_fields": ["title", "owner"]}))
    assert result.passed is False
    assert "owner" in result.diagnostic


def test_check_frontmatter_fails_without_block(project_root: Path) -> None:
    artifact = project_root / "a.md"
    _write_artifact(artifact, "no frontmatter at all\n")
    gate = get_gate("check-frontmatter")
    assert gate is not None
    result = gate(_ctx(project_root, artifact))
    assert result.passed is False
    assert "frontmatter" in result.diagnostic.lower()


# ── check-line-citations ──────────────────────────────────────────────────────


def test_check_line_citations_flags_unresolved(project_root: Path) -> None:
    # Create three real files referenced by the artifact.
    (project_root / "src").mkdir()
    (project_root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (project_root / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    (project_root / "src" / "c.py").write_text("z = 3\n", encoding="utf-8")

    artifact = project_root / "artifact.md"
    _write_artifact(
        artifact,
        (
            "See src/a.py:1 and src/b.py:1 and src/c.py:1 "
            "but also src/missing.py:42 which does not exist.\n"
        ),
    )
    gate = get_gate("check-line-citations")
    assert gate is not None
    result = gate(_ctx(project_root, artifact))
    assert result.passed is False
    assert "src/missing.py:42" in result.diagnostic
    assert result.citations is not None
    assert ("src/missing.py", 42) in result.citations


def test_check_line_citations_passes_with_no_citations(project_root: Path) -> None:
    artifact = project_root / "artifact.md"
    _write_artifact(artifact, "Just prose. No file references here.\n")
    gate = get_gate("check-line-citations")
    assert gate is not None
    result = gate(_ctx(project_root, artifact))
    assert result.passed is True


# ── check-single-flight ───────────────────────────────────────────────────────


def test_check_single_flight_passes_without_lock(project_root: Path) -> None:
    artifact = project_root / "a.md"
    _write_artifact(artifact, "body")
    gate = get_gate("check-single-flight")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, unit_id="unit-42"))
    assert result.passed is True


def test_check_single_flight_fails_when_held_by_other_pid(project_root: Path) -> None:
    unit_id = "unit-42"
    foreign_pid = os.getpid() + 1
    path = lock_path(unit_id, project_root)
    path.write_text(
        json.dumps({"unit": unit_id, "pid": foreign_pid, "acquired_at": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    artifact = project_root / "a.md"
    _write_artifact(artifact, "body")
    gate = get_gate("check-single-flight")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, unit_id=unit_id))
    assert result.passed is False
    assert str(foreign_pid) in result.diagnostic


# ── check-marker-frontmatter ──────────────────────────────────────────────────


def test_check_marker_frontmatter_passes_when_marker_parses(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_id = "unit-99"
    marker_path = project_root / ".dualpass-state" / f"{unit_id}-build-complete.md"
    marker_path.write_text("---\nunit: unit-99\nstatus: complete\n---\n", encoding="utf-8")

    def fake_read_build_marker(unit: str, stage: str, root: Path) -> BuildMarker:
        return BuildMarker(
            unit=unit,
            stage=stage,
            status="complete",
            exit_signal="stop",
            blocker_kind=None,
            artifacts_produced=[],
        )

    # Patch the symbol the builtins module imported.
    from dualpass.gates import builtins as builtins_mod

    monkeypatch.setattr(builtins_mod, "read_build_marker", fake_read_build_marker)

    artifact = project_root / "a.md"
    _write_artifact(artifact, "body")
    gate = get_gate("check-marker-frontmatter")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, unit_id=unit_id, stage="code"))
    assert result.passed is True


def test_check_marker_frontmatter_fails_when_missing(project_root: Path) -> None:
    artifact = project_root / "a.md"
    _write_artifact(artifact, "body")
    gate = get_gate("check-marker-frontmatter")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, unit_id="ghost-unit", stage="code"))
    assert result.passed is False
    assert "build-complete marker" in result.diagnostic
    assert "not found" in result.diagnostic


# ── check-acceptance-criteria-wording ─────────────────────────────────────────


def test_acceptance_criteria_wording_flags_exact_count(project_root: Path) -> None:
    artifact = project_root / "spec.md"
    _write_artifact(
        artifact,
        (
            "## Acceptance criteria\n\n"
            "AC1: exactly 12 tests pass under the new harness.\n"
            "AC2: documentation links resolve.\n"
        ),
    )
    gate = get_gate("check-acceptance-criteria-wording")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, stage="spec"))
    assert result.passed is False
    assert result.citations is not None
    assert len(result.citations) == 1
    # The citation line refers to the AC1 line; the body has "## Acceptance criteria"
    # on line 1, blank line 2, AC1 on line 3.
    assert result.citations[0][1] == 3


def test_acceptance_criteria_wording_passes_with_at_least(project_root: Path) -> None:
    artifact = project_root / "spec.md"
    _write_artifact(
        artifact,
        (
            "## Acceptance criteria\n\n"
            "AC1: at least 12 tests pass under the new harness.\n"
            "AC2: documentation links resolve.\n"
            "\n"
            "Unrelated prose mentioning the number 12 outside any AC context.\n"
        ),
    )
    gate = get_gate("check-acceptance-criteria-wording")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, stage="spec"))
    assert result.passed is True


def test_acceptance_criteria_wording_passes_without_ac_sections(project_root: Path) -> None:
    artifact = project_root / "notes.md"
    _write_artifact(
        artifact,
        (
            "# Notes\n\n"
            "There are exactly 7 things to remember.\n"
            "The team shall be 3 strong.\n"
        ),
    )
    gate = get_gate("check-acceptance-criteria-wording")
    assert gate is not None
    result = gate(_ctx(project_root, artifact, stage="spec"))
    assert result.passed is True
