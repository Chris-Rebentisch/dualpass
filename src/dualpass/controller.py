"""The deterministic loop driver — owns control flow, single-flight, breakpoints.

`run_unit(unit_id, ...)` walks the configured stage chain end-to-end:

  1. Acquire `.dualpass-state/<unit>-pipeline.lock.json` (refuse if held).
  2. Load project / agents / stages configs (raises on validation failure).
  3. For each stage in order (optionally starting from --from-stage):
       a. Check breakpoint — pause cleanly if hit (and not --ignore-breakpoints).
       b. Loop up to `max_rounds`:
            - Provider.invoke_author → artifact written under .dualpass-state/<unit>/
            - If stage has reviewer_skill:
                Provider.invoke_reviewer → verdict
                approved → break
                rejected → retry
            - If no reviewer: one round, then advance.
       c. If max_rounds exhausted with no approval → emit stage_blocked, return 1.
  4. Emit unit_completed, release lock, return 0.

The controller is provider-agnostic: it gets a Provider via `providers.get_provider`.
v0.2.0a1 ships the mock provider only; live lands later.

Not yet wired (will land in follow-up milestones):
  - Circuit breaker (no-progress detection across consecutive failed rounds)
  - Auto-relaunch on transient errors
  - Build-marker frontmatter parsing
  - Dual-pass parallel reviewer
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dualpass import config as _config
from dualpass import providers
from dualpass.memory import (
    acquire_lock,
    lock_present,
    read_lock,
    release_lock,
    state_dir,
    units_dir,
)
from dualpass.observability import Event, emit
from dualpass.providers import (
    AuthorResult,
    Provider,
    ReviewResult,
    ReviewVerdict,
    StageContext,
)

StageStatus = Literal["pending", "in_flight", "complete", "blocked", "skipped"]
ExitSignal = Literal["stop", "continue", "escalate"]


@dataclass
class StageResult:
    """Outcome of a single stage invocation."""

    stage: str
    status: StageStatus
    exit_signal: ExitSignal
    artifact_path: Path | None
    rounds_used: int
    blocker_kind: str | None = None
    blocker_detail: str | None = None


@dataclass
class UnitRun:
    """Aggregate result of `run_unit`."""

    unit_id: str
    project_root: Path
    stages_completed: list[StageResult] = field(default_factory=list)
    halted_at: str | None = None  # stage name where the run stopped (None = completed)
    halt_reason: str | None = None
    paused_at_breakpoint: str | None = None  # stage name we paused before (None = no pause)


# ── Public entry point ──────────────────────────────────────────────────────


def run_unit(
    unit_id: str,
    *,
    from_stage: str | None = None,
    provider: str = "mock",
    project_root: Path | None = None,
    ignore_breakpoints: bool = False,
) -> int:
    """Run a single unit through the configured stage chain. Returns exit code."""
    root = (project_root or Path.cwd()).resolve()

    try:
        cfg = _config.load_all(root)
    except _config.ConfigError as exc:
        print("controller: config invalid — cannot run", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err.format()}", file=sys.stderr)
        return 1

    if lock_present(unit_id, root):
        existing = read_lock(unit_id, root) or {}
        print(
            f"controller: lock already held for unit {unit_id!r} "
            f"(pid={existing.get('pid')}, acquired_at={existing.get('acquired_at')})\n"
            f"  to force-release, delete: {state_dir(root)}/{unit_id}-pipeline.lock.json",
            file=sys.stderr,
        )
        emit(
            Event("lockfile_conflict", unit=unit_id, payload={"existing": existing}),
            project_root=root,
        )
        return 2

    if not acquire_lock(unit_id, root):
        # Race-window safety net: lock_present returned False but another process
        # beat us to the atomic create.
        print(f"controller: failed to acquire lock for unit {unit_id!r}", file=sys.stderr)
        return 2
    emit(Event("lockfile_acquired", unit=unit_id), project_root=root)

    try:
        provider_impl = providers.get_provider(provider)
    except NotImplementedError as exc:
        release_lock(unit_id, root)
        emit(Event("lockfile_released", unit=unit_id), project_root=root)
        print(f"controller: {exc}", file=sys.stderr)
        return 2

    stages = cfg.stages.stages
    start_idx = _resolve_start_index(stages, from_stage)
    if start_idx is None:
        release_lock(unit_id, root)
        emit(Event("lockfile_released", unit=unit_id), project_root=root)
        valid_names = ", ".join(s.name for s in stages)
        print(
            f"controller: unknown --from-stage {from_stage!r} (valid: {valid_names})",
            file=sys.stderr,
        )
        return 2

    run = UnitRun(unit_id=unit_id, project_root=root)
    emit(
        Event(
            "unit_started",
            unit=unit_id,
            payload={
                "from_stage": from_stage,
                "provider": provider,
                "ignore_breakpoints": ignore_breakpoints,
            },
        ),
        project_root=root,
    )

    try:
        for stage in stages[start_idx:]:
            # ── Breakpoint check ──────────────────────────────────────────
            if not ignore_breakpoints and _stage_is_breakpoint(stage, cfg.project.breakpoints):
                run.paused_at_breakpoint = stage.name
                emit(
                    Event(
                        "breakpoint_hit",
                        unit=unit_id,
                        stage=stage.name,
                        payload={"breakpoint_default": stage.breakpoint_default},
                    ),
                    project_root=root,
                )
                print(
                    f"breakpoint: paused before stage {stage.name!r}\n"
                    f"  to continue: dualpass run --unit {unit_id} "
                    f"--from-stage {stage.name} --ignore-breakpoints",
                )
                return 0

            result = _run_stage(stage, provider_impl, run, cfg)
            run.stages_completed.append(result)

            if result.exit_signal == "stop":
                run.halted_at = stage.name
                run.halt_reason = result.blocker_detail or "stage signaled stop"
                print(
                    f"stage {stage.name!r} blocked after {result.rounds_used} round(s): "
                    f"{run.halt_reason}",
                    file=sys.stderr,
                )
                return 1

        emit(
            Event(
                "unit_completed",
                unit=unit_id,
                payload={"stages": [r.stage for r in run.stages_completed]},
            ),
            project_root=root,
        )
        print(
            f"unit {unit_id!r}: completed {len(run.stages_completed)} stage(s) — "
            f"{', '.join(r.stage for r in run.stages_completed)}"
        )
        return 0
    finally:
        release_lock(unit_id, root)
        emit(Event("lockfile_released", unit=unit_id), project_root=root)


# ── Internals ───────────────────────────────────────────────────────────────


def _resolve_start_index(stages: tuple, from_stage: str | None) -> int | None:
    """Return the index to start at, or None if --from-stage is unknown."""
    if from_stage is None:
        return 0
    for i, stage in enumerate(stages):
        if stage.name == from_stage:
            return i
    return None


def _stage_is_breakpoint(stage, project_breakpoints: dict[str, bool]) -> bool:
    """Honor project-level overrides; fall back to the stage default."""
    if stage.name in project_breakpoints:
        return project_breakpoints[stage.name]
    return stage.breakpoint_default


def _max_rounds_for(stage, project_cfg) -> int:
    """Look up max_rounds with project-level override → stage value → default."""
    if stage.name in project_cfg.max_revision_rounds:
        return project_cfg.max_revision_rounds[stage.name]
    if stage.max_rounds is not None:
        return stage.max_rounds
    return project_cfg.max_revision_rounds["default"]


def _run_stage(stage, provider_impl: Provider, run: UnitRun, cfg) -> StageResult:
    """Execute one stage to completion (or to max_rounds-exhausted)."""
    max_rounds = _max_rounds_for(stage, cfg.project)
    udir = units_dir(run.project_root, run.unit_id)
    rounds_used = 0
    last_artifact: AuthorResult | None = None
    last_verdict: ReviewVerdict | None = None

    for round_number in range(1, max_rounds + 1):
        rounds_used = round_number
        emit(
            Event(
                "stage_round_started",
                unit=run.unit_id,
                stage=stage.name,
                payload={"round": round_number, "max_rounds": max_rounds},
            ),
            project_root=run.project_root,
        )

        ctx = StageContext(
            unit_id=run.unit_id,
            stage=stage,
            round_number=round_number,
            units_dir=udir,
            project_root=run.project_root,
        )
        author = provider_impl.invoke_author(ctx)
        last_artifact = author

        # Some stages (e.g. `code`) intentionally have no reviewer; the next
        # stage (e.g. `audit`) is the review surface.
        if stage.reviewer_skill is None:
            emit(
                Event(
                    "stage_completed",
                    unit=run.unit_id,
                    stage=stage.name,
                    payload={
                        "round": round_number,
                        "verdict": "auto-approved (no reviewer configured)",
                        "artifact": str(author.artifact_path),
                    },
                ),
                project_root=run.project_root,
            )
            return StageResult(
                stage=stage.name,
                status="complete",
                exit_signal="continue",
                artifact_path=author.artifact_path,
                rounds_used=rounds_used,
            )

        review: ReviewResult = provider_impl.invoke_reviewer(ctx, author)
        last_verdict = review.verdict

        if review.verdict == "approved":
            emit(
                Event(
                    "stage_completed",
                    unit=run.unit_id,
                    stage=stage.name,
                    payload={
                        "round": round_number,
                        "verdict": "approved",
                        "artifact": str(author.artifact_path),
                        "review": str(review.review_artifact),
                        "served_by": review.served_by,
                    },
                ),
                project_root=run.project_root,
            )
            return StageResult(
                stage=stage.name,
                status="complete",
                exit_signal="continue",
                artifact_path=author.artifact_path,
                rounds_used=rounds_used,
            )

        # Rejected or blocked → next round (unless this was the last).
        emit(
            Event(
                "stage_revision_requested",
                unit=run.unit_id,
                stage=stage.name,
                payload={
                    "round": round_number,
                    "verdict": review.verdict,
                    "review": str(review.review_artifact),
                },
            ),
            project_root=run.project_root,
        )

    # Fell through max_rounds without approval.
    emit(
        Event(
            "stage_blocked",
            unit=run.unit_id,
            stage=stage.name,
            payload={"rounds_used": rounds_used, "last_verdict": last_verdict},
        ),
        project_root=run.project_root,
    )
    return StageResult(
        stage=stage.name,
        status="blocked",
        exit_signal="stop",
        artifact_path=last_artifact.artifact_path if last_artifact else None,
        rounds_used=rounds_used,
        blocker_kind="max_rounds_exhausted",
        blocker_detail=(
            f"reviewer returned {last_verdict!r} for {rounds_used} consecutive round(s)"
        ),
    )
