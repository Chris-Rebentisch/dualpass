"""Deterministic mock provider.

Default behavior:
  - invoke_author writes a tiny placeholder artifact and returns its path.
  - invoke_reviewer returns 'approved' on every call.

Scripted behavior:
  - Pass a `scripts` dict mapping stage name → MockScript. Each script supplies
    a list of verdicts that the reviewer cycles through. If you ask for more
    rounds than verdicts in the list, the mock sticks at the last entry.
  - This lets tests exercise revision loops (reject, reject, approve) without
    randomness.

The mock never sleeps, never touches the network, and never raises mid-run —
its job is to make the controller path testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import AuthorResult, Provider, ReviewResult, ReviewVerdict, StageContext


@dataclass
class MockScript:
    """How the mock should respond for one stage across rounds."""

    review_verdicts: list[ReviewVerdict] = field(default_factory=lambda: ["approved"])


class MockProvider(Provider):
    """Offline, deterministic provider for end-to-end controller tests."""

    def __init__(self, scripts: dict[str, MockScript] | None = None) -> None:
        self._scripts = scripts or {}
        # Per-stage cursor into the scripted verdict list.
        self._round_index: dict[str, int] = {}

    # ── Author ─────────────────────────────────────────────────────────────

    def invoke_author(self, ctx: StageContext) -> AuthorResult:
        artifact = ctx.units_dir / f"{ctx.stage.name}-artifact-v{ctx.round_number}.md"
        artifact.write_text(
            f"# Mock artifact — stage {ctx.stage.name!r}\n\n"
            f"- unit: `{ctx.unit_id}`\n"
            f"- round: {ctx.round_number}\n"
            f"- author_skill: `{ctx.stage.author_skill}`\n",
            encoding="utf-8",
        )
        return AuthorResult(artifact_path=artifact, served_by="mock")

    # ── Reviewer ───────────────────────────────────────────────────────────

    def invoke_reviewer(self, ctx: StageContext, artifact: AuthorResult) -> ReviewResult:
        script = self._scripts.get(ctx.stage.name, MockScript())
        idx = self._round_index.get(ctx.stage.name, 0)
        # If we've run past the script, stick at the last verdict (usually "approved").
        verdict = script.review_verdicts[min(idx, len(script.review_verdicts) - 1)]
        self._round_index[ctx.stage.name] = idx + 1

        suffix = f"-{ctx.pass_label}" if ctx.pass_label else ""
        review = ctx.units_dir / f"{ctx.stage.name}-review-v{ctx.round_number}{suffix}.md"
        review.write_text(
            f"# Mock review — stage {ctx.stage.name!r}\n\n"
            f"- verdict: **{verdict}**\n"
            f"- round: {ctx.round_number}\n"
            f"- reviewing: `{artifact.artifact_path.name}`\n",
            encoding="utf-8",
        )
        return ReviewResult(
            verdict=verdict,
            review_artifact=review,
            served_by="mock",
            findings=None,
        )
