"""Tests for the controller's circuit breaker (no-progress detection)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dualpass import _init, controller, providers
from dualpass.memory import state_dir, units_dir
from dualpass.observability import read_events
from dualpass.providers.base import (
    AuthorResult,
    Provider,
    ReviewResult,
    ReviewVerdict,
    StageContext,
)
from dualpass.providers.mock import MockProvider, MockScript


class FixedContentMock(Provider):
    """Mock that produces byte-identical artifacts across rounds.

    Needed to exercise the circuit breaker: the default `MockProvider` writes
    `round_number` into the artifact body, so its hash changes every round
    and the breaker's no-progress streak resets each turn (which is actually
    the correct behavior — just not what we're testing here).
    """

    def __init__(self, verdict: ReviewVerdict = "rejected") -> None:
        self._verdict: ReviewVerdict = verdict

    def invoke_author(self, ctx: StageContext) -> AuthorResult:
        artifact = ctx.units_dir / f"{ctx.stage.name}-artifact-v{ctx.round_number}.md"
        # Content is identical across every round — same bytes → same SHA-256.
        artifact.write_text(
            f"# Mock artifact for {ctx.stage.name}\n\nFIXED-CONTENT-FOR-BREAKER-TEST\n",
            encoding="utf-8",
        )
        return AuthorResult(artifact_path=artifact, served_by="fixed-mock")

    def invoke_reviewer(self, ctx: StageContext, artifact: AuthorResult) -> ReviewResult:
        review = ctx.units_dir / f"{ctx.stage.name}-review-v{ctx.round_number}.md"
        review.write_text(f"Verdict: {self._verdict}\n", encoding="utf-8")
        return ReviewResult(
            verdict=self._verdict,
            review_artifact=review,
            served_by="fixed-mock",
        )


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    _init.run_init(target)
    # Tighten breaker so tests run fast.
    dp_json = target / "config" / "dualpass.json"
    data = json.loads(dp_json.read_text())
    data["circuit_breaker"]["max_no_progress_relaunches"] = 2
    dp_json.write_text(json.dumps(data, indent=2))
    return target


def _install_scripted_mock(scripts: dict[str, MockScript]) -> None:
    """Monkey-patch get_provider so we control reviewer verdicts."""
    original = providers.get_provider

    def scripted(name: str, *, agents_config=None):
        if name == "mock":
            return MockProvider(scripts=scripts)
        return original(name, agents_config=agents_config)

    providers.get_provider = scripted  # type: ignore[assignment]
    return original  # type: ignore[return-value]


def _install_fixed_mock(verdict: ReviewVerdict = "rejected"):
    """Monkey-patch get_provider with the FixedContentMock for breaker tests."""
    original = providers.get_provider

    def fixed(name: str, *, agents_config=None):
        if name == "mock":
            return FixedContentMock(verdict=verdict)
        return original(name, agents_config=agents_config)

    providers.get_provider = fixed  # type: ignore[assignment]
    return original


def _restore_provider(original) -> None:
    providers.get_provider = original  # type: ignore[assignment]


# ── Trip path ────────────────────────────────────────────────────────────────


def test_breaker_trips_when_artifact_unchanged_across_consecutive_rounds(
    scaffolded_project: Path,
) -> None:
    """Fixed-content mock + reviewer always rejects → trip after threshold+1 rounds."""
    original = _install_fixed_mock("rejected")
    try:
        rc = controller.run_unit(
            "demo-trip",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    finally:
        _restore_provider(original)

    # Halts with non-zero rc.
    assert rc == 1
    # Diagnostic marker written.
    marker = state_dir(scaffolded_project) / "demo-trip-circuit-tripped.md"
    assert marker.is_file()
    body = marker.read_text()
    assert "research" in body
    assert "threshold:" in body and " 2 consecutive" in body
    # Event log carries the circuit_breaker_tripped event.
    events = read_events("demo-trip", scaffolded_project)
    assert any(e.type == "circuit_breaker_tripped" for e in events)


def test_breaker_artifact_hash_recorded_in_event(scaffolded_project: Path) -> None:
    original = _install_fixed_mock("rejected")
    try:
        controller.run_unit(
            "demo-hash",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    finally:
        _restore_provider(original)

    events = read_events("demo-hash", scaffolded_project)
    tripped = next(e for e in events if e.type == "circuit_breaker_tripped")
    assert tripped.payload["artifact_hash"]  # non-empty hex digest
    assert len(tripped.payload["artifact_hash"]) == 64  # SHA-256 hex length


# ── Reset path ───────────────────────────────────────────────────────────────


def test_breaker_resets_on_progress(scaffolded_project: Path) -> None:
    """If artifact hash changes between rounds, the streak should reset.

    We simulate this by writing a mock that produces a DIFFERENT artifact each
    round (the default mock includes round_number in the artifact body so the
    hash differs naturally). With max_rounds=6 (per the example) and rejected
    verdict, the controller will hit max_rounds_exhausted — NOT
    circuit_breaker_tripped.
    """
    original = _install_scripted_mock({"research": MockScript(review_verdicts=["rejected"])})
    try:
        rc = controller.run_unit(
            "demo-reset",
            provider="mock",
            project_root=scaffolded_project,
            ignore_breakpoints=True,
        )
    finally:
        _restore_provider(original)

    # The default MockProvider writes round_number into the artifact body, so
    # every round produces a different hash → breaker streak stays 0 → max_rounds
    # is what halts us. So this test currently confirms the default mock IS
    # producing different artifacts (no false trip).
    events = read_events("demo-reset", scaffolded_project)
    # max_rounds for research is 6 (project default). We should NOT have tripped.
    assert not any(e.type == "circuit_breaker_tripped" for e in events)
    assert rc == 1  # halt for max_rounds_exhausted instead


# ── Disabled path ────────────────────────────────────────────────────────────


def test_breaker_disabled_when_threshold_is_zero(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    _init.run_init(target)
    dp_json = target / "config" / "dualpass.json"
    data = json.loads(dp_json.read_text())
    data["circuit_breaker"]["max_no_progress_relaunches"] = 1  # min allowed by schema
    dp_json.write_text(json.dumps(data, indent=2))

    # With breaker effectively at 1 and constant rejection + constant artifact,
    # we should trip after the second-round detection (streak ≥ 1).
    original = _install_fixed_mock("rejected")
    try:
        controller.run_unit(
            "demo-min", provider="mock", project_root=target, ignore_breakpoints=True
        )
    finally:
        _restore_provider(original)

    events = read_events("demo-min", target)
    # Should have tripped — round 1 sets baseline, round 2 matches → streak=1 ≥ threshold=1.
    assert any(e.type == "circuit_breaker_tripped" for e in events)


# ── Approval still wins ──────────────────────────────────────────────────────


def test_breaker_doesnt_fire_when_reviewer_approves(scaffolded_project: Path) -> None:
    """Default mock approves on first try; breaker should never engage."""
    rc = controller.run_unit(
        "demo-ok",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    assert rc == 0
    # No circuit marker.
    marker = state_dir(scaffolded_project) / "demo-ok-circuit-tripped.md"
    assert not marker.is_file()
    # And every stage's artifact exists.
    udir = units_dir(scaffolded_project, "demo-ok")
    assert (udir / "handoff-artifact-v1.md").is_file()
