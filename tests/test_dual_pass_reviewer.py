"""Tests for the dual-pass parallel reviewer (stage.dual_pass_reviewer = true)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dualpass import _init, controller, providers
from dualpass.memory import BuildMarker, units_dir, write_build_marker
from dualpass.observability import read_events
from dualpass.providers.base import (
    AuthorResult,
    Provider,
    ReviewResult,
    ReviewVerdict,
    StageContext,
)


class PerLabelMock(Provider):
    """Mock that returns different verdicts based on pass_label.

    Reviews are written to {stage}-review-v{round}-{pass_label}.md (when
    pass_label is set on the StageContext). Verdicts come from `verdicts_by_label`
    so a test can simulate 'a approves, b rejects' to verify both must pass.
    """

    def __init__(self, verdicts_by_label: dict[str, ReviewVerdict] | None = None) -> None:
        self._verdicts_by_label: dict[str, ReviewVerdict] = verdicts_by_label or {
            "a": "approved",
            "b": "approved",
        }

    def invoke_author(self, ctx: StageContext) -> AuthorResult:
        artifact = ctx.units_dir / f"{ctx.stage.name}-artifact-v{ctx.round_number}.md"
        # Write YAML frontmatter so `check-frontmatter` preflight passes.
        artifact.write_text(
            f"---\ntitle: Mock {ctx.stage.name}\nstage: {ctx.stage.name}\n---\n\n"
            f"# Mock for {ctx.stage.name}\n",
            encoding="utf-8",
        )
        # Satisfy `check-marker-frontmatter` with a benign continue marker.
        write_build_marker(
            BuildMarker(
                unit=ctx.unit_id,
                stage=ctx.stage.name,
                status="complete",
                exit_signal="continue",
            ),
            ctx.project_root,
        )
        return AuthorResult(artifact_path=artifact, served_by="per-label-mock")

    def invoke_reviewer(self, ctx: StageContext, artifact: AuthorResult) -> ReviewResult:
        label = ctx.pass_label or "single"
        suffix = f"-{label}" if ctx.pass_label else ""
        verdict = self._verdicts_by_label.get(label, "approved")
        review = ctx.units_dir / f"{ctx.stage.name}-review-v{ctx.round_number}{suffix}.md"
        review.write_text(f"Verdict: {verdict}\nServed: {label}\n", encoding="utf-8")
        return ReviewResult(
            verdict=verdict,
            review_artifact=review,
            served_by=f"per-label-{label}",
        )


def _install_mock(impl: Provider) -> None:
    original = providers.get_provider

    def factory(name: str, *, agents_config=None):
        if name == "mock":
            return impl
        return original(name, agents_config=agents_config)

    providers.get_provider = factory  # type: ignore[assignment]
    return original  # type: ignore[return-value]


def _restore(original) -> None:
    providers.get_provider = original  # type: ignore[assignment]


@pytest.fixture
def project_with_dual_pass_spec(tmp_path: Path) -> Path:
    """Scaffold the example — its `spec` stage already has dual_pass_reviewer: true."""
    from tests.conftest import enable_research_reviewer
    target = tmp_path / "proj"
    _init.run_init(target)
    enable_research_reviewer(target)
    return target


# ── Happy path ────────────────────────────────────────────────────────────────


def test_dual_pass_writes_two_reviews_with_a_and_b_suffixes(
    project_with_dual_pass_spec: Path,
) -> None:
    """When both reviewers approve, controller advances and BOTH artifact files exist."""
    original = _install_mock(PerLabelMock({"a": "approved", "b": "approved"}))
    try:
        rc = controller.run_unit(
            "demo-dp-ok",
            provider="mock",
            project_root=project_with_dual_pass_spec,
            ignore_breakpoints=True,
        )
    finally:
        _restore(original)

    assert rc == 0
    udir = units_dir(project_with_dual_pass_spec, "demo-dp-ok")
    # Spec is the dual-pass stage in the bundled example.
    assert (udir / "spec-review-v1-a.md").is_file()
    assert (udir / "spec-review-v1-b.md").is_file()
    # Other stages are single-pass; no -a/-b files for them.
    assert not (udir / "research-review-v1-a.md").is_file()
    assert (udir / "research-review-v1.md").is_file()


def test_dual_pass_rejects_when_one_reviewer_rejects(
    project_with_dual_pass_spec: Path,
) -> None:
    """If A approves but B rejects, the round counts as rejected and we retry."""
    original = _install_mock(PerLabelMock({"a": "approved", "b": "rejected"}))
    try:
        rc = controller.run_unit(
            "demo-dp-split",
            provider="mock",
            project_root=project_with_dual_pass_spec,
            ignore_breakpoints=True,
        )
    finally:
        _restore(original)

    # Should halt because reviewer B keeps rejecting every round.
    assert rc == 1
    events = read_events("demo-dp-split", project_with_dual_pass_spec)
    # The spec stage should have emitted revision_requested for several rounds.
    revisions = [e for e in events if e.type == "stage_revision_requested" and e.stage == "spec"]
    assert len(revisions) >= 1
    # And the dual_pass marker should appear in the payload.
    assert any(e.payload.get("dual_pass") is True for e in revisions)


def test_single_pass_stage_unaffected(project_with_dual_pass_spec: Path) -> None:
    """Non-dual-pass stages should still produce one review file without suffix."""
    original = _install_mock(PerLabelMock({"a": "approved", "b": "approved"}))
    try:
        controller.run_unit(
            "demo-single",
            provider="mock",
            project_root=project_with_dual_pass_spec,
            ignore_breakpoints=True,
        )
    finally:
        _restore(original)

    udir = units_dir(project_with_dual_pass_spec, "demo-single")
    # research, outline, audit, handoff are all single-pass — no suffix.
    for stage in ("research", "outline", "audit", "handoff"):
        assert (udir / f"{stage}-review-v1.md").is_file(), f"missing review for {stage}"
        assert not (udir / f"{stage}-review-v1-a.md").is_file()


def test_dual_pass_event_marks_dual_pass_true(project_with_dual_pass_spec: Path) -> None:
    """The stage_completed event for a dual-pass stage should carry dual_pass=true."""
    original = _install_mock(PerLabelMock({"a": "approved", "b": "approved"}))
    try:
        controller.run_unit(
            "demo-dp-event",
            provider="mock",
            project_root=project_with_dual_pass_spec,
            ignore_breakpoints=True,
        )
    finally:
        _restore(original)

    events = read_events("demo-dp-event", project_with_dual_pass_spec)
    completed = [e for e in events if e.type == "stage_completed"]
    spec_completed = next(e for e in completed if e.stage == "spec")
    assert spec_completed.payload["dual_pass"] is True
    # Two reviewer artifacts should be referenced.
    assert "-a.md" in spec_completed.payload["review"]
    assert "-b.md" in spec_completed.payload["review"]
    # served_by should reflect both reviewer labels.
    assert "per-label-a" in spec_completed.payload["served_by"]
    assert "per-label-b" in spec_completed.payload["served_by"]
