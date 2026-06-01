"""Deterministic mock provider.

Default behavior:
  - invoke_author writes a tiny placeholder artifact and returns its path.
  - invoke_author also writes a minimal `<unit>-build-complete.md` marker so
    the bundled example's `check-marker-frontmatter` preflight gate (used by
    the code stage) is satisfied without per-test setup. The default marker
    has `exit_signal: continue` so it never halts the loop.
  - invoke_reviewer returns 'approved' on every call.

Scripted behavior:
  - Pass a `scripts` dict mapping stage name → MockScript. Each script supplies
    a list of verdicts that the reviewer cycles through. If you ask for more
    rounds than verdicts in the list, the mock sticks at the last entry.
  - Pass a `markers` dict mapping stage name → MockMarker to override the
    default-continue marker for that stage. Use this to drive the
    author-driven halt path (stop/escalate) in tests without monkey-patching.
  - This lets tests exercise revision loops (reject, reject, approve) without
    randomness.

The mock never sleeps, never touches the network, and never raises mid-run —
its job is to make the controller path testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from dualpass.memory import BuildMarker, write_build_marker

from .base import AuthorResult, Provider, ReviewResult, ReviewVerdict, StageContext


@dataclass
class MockScript:
    """How the mock should respond for one stage across rounds."""

    review_verdicts: list[ReviewVerdict] = field(default_factory=lambda: ["approved"])


@dataclass
class MockMarker:
    """Per-stage override for the build-complete marker the mock writes.

    The mock writes a default `exit_signal: continue` marker so the bundled
    example's `check-marker-frontmatter` gate passes. Tests that want to drive
    the stop/escalate paths pass an entry here.
    """

    exit_signal: Literal["stop", "continue", "escalate"] = "continue"
    status: Literal["partial", "complete", "blocked"] = "complete"
    blocker_kind: str | None = None
    reason: str | None = None


class MockProvider(Provider):
    """Offline, deterministic provider for end-to-end controller tests."""

    def __init__(
        self,
        scripts: dict[str, MockScript] | None = None,
        markers: dict[str, MockMarker] | None = None,
    ) -> None:
        self._scripts = scripts or {}
        self._markers = markers or {}
        # Per-stage cursor into the scripted verdict list.
        self._round_index: dict[str, int] = {}

    # ── Author ─────────────────────────────────────────────────────────────

    def invoke_author(self, ctx: StageContext) -> AuthorResult:
        artifact = ctx.units_dir / f"{ctx.stage.name}-artifact-v{ctx.round_number}.md"
        # Write a small YAML frontmatter block so the artifact satisfies the
        # built-in `check-frontmatter` preflight gate. Real author skills are
        # expected to produce richer frontmatter; the mock only needs enough to
        # exercise the controller's wiring end-to-end.
        artifact.write_text(
            f"---\n"
            f"title: Mock {ctx.stage.name} artifact\n"
            f"unit: {ctx.unit_id}\n"
            f"stage: {ctx.stage.name}\n"
            f"round: {ctx.round_number}\n"
            f"---\n\n"
            f"# Mock artifact — stage {ctx.stage.name!r}\n\n"
            f"- unit: `{ctx.unit_id}`\n"
            f"- round: {ctx.round_number}\n"
            f"- author_skill: `{ctx.stage.author_skill}`\n",
            encoding="utf-8",
        )
        # Write the build-complete marker after the artifact lands. Default is
        # a continue-signal marker so `check-marker-frontmatter` passes and the
        # controller proceeds to the reviewer; tests can override per stage.
        self._write_marker(ctx)
        return AuthorResult(artifact_path=artifact, served_by="mock")

    def _write_marker(self, ctx: StageContext) -> None:
        """Persist a build-complete marker reflecting the configured override."""
        override = self._markers.get(ctx.stage.name)
        if override is None:
            marker = BuildMarker(
                unit=ctx.unit_id,
                stage=ctx.stage.name,
                status="complete",
                exit_signal="continue",
                blocker_kind=None,
                artifacts_produced=[],
                metadata={},
            )
        else:
            blocker = override.blocker_kind
            if blocker is not None and blocker not in (
                "architectural",
                "infrastructure",
                "spec_defect",
                "max_rounds_exhausted",
            ):
                # Forward-compatible fallback: an unrecognised blocker kind goes
                # into metadata instead of failing the marker write.
                blocker = None
            metadata: dict[str, Any] = {}
            if override.reason is not None:
                metadata["reason"] = override.reason
            marker = BuildMarker(
                unit=ctx.unit_id,
                stage=ctx.stage.name,
                status=override.status,
                exit_signal=override.exit_signal,
                blocker_kind=blocker,  # type: ignore[arg-type]
                artifacts_produced=[],
                metadata=metadata,
            )
        write_build_marker(marker, ctx.project_root)

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
