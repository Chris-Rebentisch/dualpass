"""Tests for the deterministic mock provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from dualpass.config import StageConfig
from dualpass.providers import MockProvider, MockScript, ReviewVerdict, StageContext


def _make_ctx(tmp_path: Path, stage_name: str = "research", round_number: int = 1) -> StageContext:
    return StageContext(
        unit_id="demo-001",
        stage=StageConfig(name=stage_name, author_skill=f"skills/{stage_name}/SKILL.md"),
        round_number=round_number,
        units_dir=tmp_path,
        project_root=tmp_path.parent,
    )


def test_invoke_author_writes_artifact_under_units_dir(tmp_path: Path) -> None:
    provider = MockProvider()
    ctx = _make_ctx(tmp_path, "research", 1)
    result = provider.invoke_author(ctx)
    assert result.artifact_path.is_file()
    assert result.artifact_path.parent == tmp_path
    assert "research" in result.artifact_path.read_text()
    assert result.served_by == "mock"


def test_invoke_reviewer_default_returns_approved(tmp_path: Path) -> None:
    provider = MockProvider()
    ctx = _make_ctx(tmp_path)
    author = provider.invoke_author(ctx)
    review = provider.invoke_reviewer(ctx, author)
    assert review.verdict == "approved"
    assert review.review_artifact.is_file()
    assert "verdict" in review.review_artifact.read_text()


def test_invoke_reviewer_cycles_through_scripted_verdicts(tmp_path: Path) -> None:
    """Scripted: reject twice, then approve. Mock should walk that sequence."""
    script = MockScript(review_verdicts=["rejected", "rejected", "approved"])
    provider = MockProvider(scripts={"spec": script})

    verdicts: list[ReviewVerdict] = []
    for round_number in (1, 2, 3):
        ctx = _make_ctx(tmp_path, "spec", round_number)
        author = provider.invoke_author(ctx)
        review = provider.invoke_reviewer(ctx, author)
        verdicts.append(review.verdict)
    assert verdicts == ["rejected", "rejected", "approved"]


def test_invoke_reviewer_sticks_at_last_verdict_when_run_past_script(tmp_path: Path) -> None:
    """Script says ['approved']. If asked for 3 rounds, all 3 return approved."""
    provider = MockProvider(scripts={"outline": MockScript(review_verdicts=["approved"])})
    for round_number in (1, 2, 3):
        ctx = _make_ctx(tmp_path, "outline", round_number)
        author = provider.invoke_author(ctx)
        review = provider.invoke_reviewer(ctx, author)
        assert review.verdict == "approved"


def test_different_stages_have_independent_round_state(tmp_path: Path) -> None:
    """Cursor for stage A must not advance when stage B is invoked."""
    provider = MockProvider(
        scripts={
            "research": MockScript(review_verdicts=["rejected", "approved"]),
            "outline": MockScript(review_verdicts=["rejected", "approved"]),
        }
    )
    # research round 1 → rejected; outline round 1 → rejected; research round 2 → approved.
    r1 = provider.invoke_reviewer(
        _make_ctx(tmp_path, "research", 1),
        provider.invoke_author(_make_ctx(tmp_path, "research", 1)),
    )
    o1 = provider.invoke_reviewer(
        _make_ctx(tmp_path, "outline", 1), provider.invoke_author(_make_ctx(tmp_path, "outline", 1))
    )
    r2 = provider.invoke_reviewer(
        _make_ctx(tmp_path, "research", 2),
        provider.invoke_author(_make_ctx(tmp_path, "research", 2)),
    )
    assert (r1.verdict, o1.verdict, r2.verdict) == ("rejected", "rejected", "approved")


def test_get_provider_resolves_mock_and_rejects_unknown() -> None:
    from dualpass.providers import get_provider

    assert isinstance(get_provider("mock"), MockProvider)
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider("does-not-exist")


def test_get_provider_live_requires_agents_config() -> None:
    """The live provider exists; it needs agents_config to wire up CLIs."""
    from dualpass.providers import get_provider

    with pytest.raises(ValueError, match="requires agents_config"):
        get_provider("live")
