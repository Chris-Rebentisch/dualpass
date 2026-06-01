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
Both `mock` (deterministic, offline) and `live` (real subprocess agent invocations
with cross-vendor fallback) ship in v1.

Wired in v1:
  - Circuit breaker (no-progress detection across consecutive failed rounds)
  - Cross-vendor reviewer fallback (in the live provider)
  - Dual-pass parallel reviewer (when `stage.dual_pass_reviewer: true`)
  - Single-flight lockfile with atomic O_CREAT|O_EXCL acquire
  - Breakpoint pause + `--from-stage` resume

Out of scope for v1 (signature stubs only):
  - `memory.read_build_marker` — the event log is the canonical state source.
  - `context.build_stage_context` / `build_precedent_cache` — stage skills
    curate context inline per SKILL.md.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
        provider_impl = providers.get_provider(provider, agents_config=cfg.agents)
    except (NotImplementedError, providers.LiveProviderError, ValueError) as exc:
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


def _invoke_dual_reviewers(
    provider_impl: Provider, ctx: StageContext, author: AuthorResult
) -> list[ReviewResult]:
    """Spawn two reviewer invocations in parallel; return both results in order.

    Both reviewers see the same prompt (same skill, same artifact). The
    independence comes from LLM nondeterminism and — for the live provider —
    the cross-vendor fallback mechanic. Both reviewers must return `approved`
    for the stage round to pass. Distinct `pass_label` values disambiguate
    the per-reviewer artifact filenames so the two concurrent writes don't
    collide on disk.
    """
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    ctx_a = replace(ctx, pass_label="a")
    ctx_b = replace(ctx, pass_label="b")
    with ThreadPoolExecutor(max_workers=2) as ex:
        future_a = ex.submit(provider_impl.invoke_reviewer, ctx_a, author)
        future_b = ex.submit(provider_impl.invoke_reviewer, ctx_b, author)
        return [future_a.result(), future_b.result()]


def _hash_file(path: Path) -> str:
    """SHA-256 of a file's bytes. Used by the circuit breaker."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _write_circuit_breaker_marker(
    project_root: Path,
    unit_id: str,
    stage_name: str,
    rounds_used: int,
    threshold: int,
    artifact_hash: str,
) -> None:
    """Drop a human-readable diagnostic next to the state dir on trip."""
    from dualpass.memory import state_dir

    marker = state_dir(project_root) / f"{unit_id}-circuit-tripped.md"
    marker.write_text(
        f"# Circuit breaker tripped\n\n"
        f"- **unit:** `{unit_id}`\n"
        f"- **stage:** `{stage_name}`\n"
        f"- **rounds used:** {rounds_used}\n"
        f"- **threshold:** {threshold} consecutive no-progress rounds\n"
        f"- **artifact hash (sha256):** `{artifact_hash}`\n"
        f"- **tripped at:** {datetime.now(UTC).isoformat()}\n\n"
        f"## What this means\n\n"
        f"The author kept producing the same artifact (identical SHA-256) while "
        f"the reviewer kept rejecting it. Continuing to retry would burn tokens "
        f"and time without forward progress. The controller halted the run.\n\n"
        f"## What to do\n\n"
        f"1. Inspect the artifact at `.dualpass-state/{unit_id}/{stage_name}-artifact-v{rounds_used}.md`\n"
        f"2. Inspect the review at `.dualpass-state/{unit_id}/{stage_name}-review-v{rounds_used}.md`\n"
        f"3. Decide whether the reviewer is being unreasonable, the author lacks "
        f"context, or the stage skill itself needs editing\n"
        f"4. Address the root cause, then re-run with: "
        f"`dualpass run --unit {unit_id} --from-stage {stage_name}`\n",
        encoding="utf-8",
    )


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
    """Execute one stage to completion (max_rounds-exhausted, or circuit-tripped)."""
    max_rounds = _max_rounds_for(stage, cfg.project)
    udir = units_dir(run.project_root, run.unit_id)
    rounds_used = 0
    last_artifact: AuthorResult | None = None
    last_verdict: ReviewVerdict | None = None

    # Circuit breaker state — track per-stage. Reads project-level config; if
    # the operator hasn't enabled the breaker (max_no_progress_relaunches=0 or
    # missing) we skip the check entirely.
    breaker_threshold = int(cfg.project.circuit_breaker.get("max_no_progress_relaunches", 0) or 0)
    no_progress_streak = 0
    last_artifact_hash: str | None = None

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

        # Hash the artifact body to drive circuit-breaker progress detection.
        # We rehash every round; if identical content shows up `breaker_threshold`
        # times in a row AND the reviewer keeps rejecting, we halt and write a
        # diagnostic marker. A successful approval below also returns out before
        # ever incrementing the streak, so a one-shot stage trivially escapes.
        if breaker_threshold > 0:
            current_hash = _hash_file(author.artifact_path)
            if last_artifact_hash is not None and current_hash == last_artifact_hash:
                no_progress_streak += 1
            else:
                no_progress_streak = 0
            last_artifact_hash = current_hash

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

        # Reviewer pass — single-vendor or dual-vendor parallel depending on
        # the stage's `dual_pass_reviewer` flag. Dual-pass requires BOTH
        # reviewers to approve; if either rejects, the stage round counts as
        # rejected and we retry up to max_rounds.
        if stage.dual_pass_reviewer:
            reviews = _invoke_dual_reviewers(provider_impl, ctx, author)
            review_artifacts = [r.review_artifact for r in reviews]
            verdicts = [r.verdict for r in reviews]
            served_by = ", ".join(r.served_by for r in reviews)
            combined_verdict: ReviewVerdict = (
                "approved" if all(v == "approved" for v in verdicts) else "rejected"
            )
            review = reviews[0]  # representative for downstream payload references
            review_repr = "; ".join(str(a) for a in review_artifacts)
        else:
            review = provider_impl.invoke_reviewer(ctx, author)
            served_by = review.served_by
            combined_verdict = review.verdict
            review_repr = str(review.review_artifact)
        last_verdict = combined_verdict

        if combined_verdict == "approved":
            emit(
                Event(
                    "stage_completed",
                    unit=run.unit_id,
                    stage=stage.name,
                    payload={
                        "round": round_number,
                        "verdict": "approved",
                        "artifact": str(author.artifact_path),
                        "review": review_repr,
                        "served_by": served_by,
                        "dual_pass": stage.dual_pass_reviewer,
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
                    "verdict": combined_verdict,
                    "review": review_repr,
                    "no_progress_streak": no_progress_streak,
                    "dual_pass": stage.dual_pass_reviewer,
                },
            ),
            project_root=run.project_root,
        )

        # Circuit breaker: bail out if the author keeps producing the same
        # artifact AND the reviewer keeps rejecting. Threshold is "consecutive
        # no-progress rounds" — a single no-progress round (streak == 1) is
        # normal, only N+1 in a row trips it.
        if breaker_threshold > 0 and no_progress_streak >= breaker_threshold:
            _write_circuit_breaker_marker(
                run.project_root,
                run.unit_id,
                stage.name,
                rounds_used,
                breaker_threshold,
                last_artifact_hash or "",
            )
            emit(
                Event(
                    "circuit_breaker_tripped",
                    unit=run.unit_id,
                    stage=stage.name,
                    payload={
                        "rounds_used": rounds_used,
                        "threshold": breaker_threshold,
                        "artifact_hash": last_artifact_hash,
                    },
                ),
                project_root=run.project_root,
            )
            return StageResult(
                stage=stage.name,
                status="blocked",
                exit_signal="stop",
                artifact_path=author.artifact_path,
                rounds_used=rounds_used,
                blocker_kind="circuit_breaker_tripped",
                blocker_detail=(
                    f"author produced identical artifact for {no_progress_streak + 1} "
                    f"consecutive rounds while reviewer kept rejecting "
                    f"(threshold={breaker_threshold})"
                ),
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
