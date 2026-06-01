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
import logging
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dualpass import config as _config
from dualpass import providers
from dualpass.context import build_precedent_cache, build_stage_context
from dualpass.gates import GateContext, GateResult, run_gates
from dualpass.memory import (
    BuildMarker,
    BuildMarkerError,
    acquire_lock,
    lock_present,
    read_build_marker,
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

logger = logging.getLogger(__name__)

StageStatus = Literal["pending", "in_flight", "complete", "blocked", "skipped"]
ExitSignal = Literal["stop", "continue", "escalate"]

# (v1.0.5) Audit verdicts the controller routes on. The audit skill's machine-stable
# verdict line names exactly one of these; everything else falls back to "unknown"
# and is treated as a halt for operator inspection.
AuditVerdict = Literal["pass", "needs_remediation", "architectural_divergence", "unknown"]

# Pre-compiled regex matches `**Verdict:** PASS|NEEDS_REMEDIATION|ARCHITECTURAL_DIVERGENCE`
# anywhere in the audit FINAL body. Case-insensitive; permits surrounding whitespace.
_AUDIT_VERDICT_RE = re.compile(
    r"^\s*\*\*Verdict:\*\*\s*(PASS|NEEDS_REMEDIATION|ARCHITECTURAL_DIVERGENCE)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# The stage name that consumes audit feedback when the controller routes a
# NEEDS_REMEDIATION verdict back through the loop. The bundled example uses
# "code"; projects can override their stage name via the same constant.
_CODE_STAGE_NAME = "code"
_AUDIT_STAGE_NAME = "audit"

# Stages for which a precedent-cache bundle is built ahead of the author.
# These are the stages where peer artifacts from prior units carry the most
# signal — for purely structural stages (audit, handoff) the predecessor
# artifact already in the stage-context bundle is enough.
PRECEDENT_STAGES: frozenset[str] = frozenset({"outline", "spec", "prompt"})


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

    # (v1.0.5) Per-unit audit-loop counter. Bounded by cfg.project.max_audit_iterations
    # (default 4). When the budget exhausts, the controller writes an audit-loop-exhausted
    # stuck marker and halts for architect attention.
    audit_iterations = 0
    max_audit_iterations = int(getattr(cfg.project, "max_audit_iterations", 4) or 4)

    try:
        idx = start_idx
        while idx < len(stages):
            stage = stages[idx]
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

            # ── (v1.0.5) audit-verdict routing ─────────────────────────────
            # After the audit stage approves (the reviewer signed off on the
            # audit artifact's structure), parse the audit's machine-stable
            # verdict and route the unit accordingly. PASS continues to handoff;
            # NEEDS_REMEDIATION rewinds to code; ARCHITECTURAL_DIVERGENCE halts.
            if stage.name == _AUDIT_STAGE_NAME and result.artifact_path is not None:
                audit_verdict = _parse_audit_verdict(result.artifact_path)
                code_idx = _find_stage_index(stages, _CODE_STAGE_NAME)
                emit(
                    Event(
                        "audit_verdict_parsed",
                        unit=unit_id,
                        stage=stage.name,
                        payload={
                            "verdict": audit_verdict,
                            "audit_artifact": str(result.artifact_path),
                            "audit_iterations": audit_iterations,
                            "max_audit_iterations": max_audit_iterations,
                        },
                    ),
                    project_root=root,
                )

                if audit_verdict == "pass":
                    idx += 1
                    continue

                if audit_verdict == "needs_remediation":
                    if code_idx is None:
                        # No code stage to rewind to — treat as unknown.
                        print(
                            f"audit returned NEEDS_REMEDIATION but no "
                            f"{_CODE_STAGE_NAME!r} stage exists to remediate; halting.",
                            file=sys.stderr,
                        )
                        run.halted_at = stage.name
                        run.halt_reason = "needs_remediation without code stage"
                        return 1
                    audit_iterations += 1
                    if audit_iterations > max_audit_iterations:
                        marker = _write_audit_routing_marker(
                            root,
                            unit_id,
                            kind="audit-loop-exhausted",
                            audit_artifact=result.artifact_path,
                            audit_iterations=audit_iterations - 1,
                            max_iterations=max_audit_iterations,
                        )
                        emit(
                            Event(
                                "stage_blocked",
                                unit=unit_id,
                                stage=stage.name,
                                payload={
                                    "reason": "audit_loop_exhausted",
                                    "audit_iterations": audit_iterations - 1,
                                    "max_audit_iterations": max_audit_iterations,
                                    "stuck_marker": str(marker),
                                },
                            ),
                            project_root=root,
                        )
                        run.halted_at = stage.name
                        run.halt_reason = (
                            f"audit_loop_exhausted ({audit_iterations - 1}/{max_audit_iterations})"
                        )
                        print(
                            f"unit {unit_id!r}: audit loop exhausted "
                            f"({audit_iterations - 1}/{max_audit_iterations}); "
                            f"see {marker}",
                            file=sys.stderr,
                        )
                        return 1
                    print(
                        f"audit verdict NEEDS_REMEDIATION; "
                        f"re-entering {_CODE_STAGE_NAME!r} stage "
                        f"(iteration {audit_iterations}/{max_audit_iterations})"
                    )
                    emit(
                        Event(
                            "audit_remediation_loop",
                            unit=unit_id,
                            stage=stage.name,
                            payload={
                                "audit_iterations": audit_iterations,
                                "max_audit_iterations": max_audit_iterations,
                                "re_entering_stage": _CODE_STAGE_NAME,
                            },
                        ),
                        project_root=root,
                    )
                    idx = code_idx
                    continue

                if audit_verdict == "architectural_divergence":
                    marker = _write_audit_routing_marker(
                        root,
                        unit_id,
                        kind="architectural-divergence",
                        audit_artifact=result.artifact_path,
                        audit_iterations=audit_iterations,
                        max_iterations=max_audit_iterations,
                    )
                    emit(
                        Event(
                            "stage_blocked",
                            unit=unit_id,
                            stage=stage.name,
                            payload={
                                "reason": "architectural_divergence",
                                "stuck_marker": str(marker),
                            },
                        ),
                        project_root=root,
                    )
                    run.halted_at = stage.name
                    run.halt_reason = "architectural_divergence"
                    print(
                        f"unit {unit_id!r}: audit reported ARCHITECTURAL_DIVERGENCE; "
                        f"architect intervention required\n  see {marker}",
                        file=sys.stderr,
                    )
                    return 1

                # `unknown` — audit FINAL has no recognizable verdict line.
                # Halt for operator inspection rather than guess.
                emit(
                    Event(
                        "stage_blocked",
                        unit=unit_id,
                        stage=stage.name,
                        payload={"reason": "audit_verdict_unknown"},
                    ),
                    project_root=root,
                )
                run.halted_at = stage.name
                run.halt_reason = "audit_verdict_unknown"
                print(
                    f"unit {unit_id!r}: audit FINAL carries no recognizable verdict "
                    f"line; halting.\n  audit artifact: {result.artifact_path}",
                    file=sys.stderr,
                )
                return 1

            idx += 1

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


def _predecessor_stage_for(stages: tuple, current_name: str) -> str | None:
    """Return the configured `requires_predecessor` (or position-derived prior)."""
    for i, stage in enumerate(stages):
        if stage.name != current_name:
            continue
        if stage.requires_predecessor:
            return stage.requires_predecessor
        if i > 0:
            return stages[i - 1].name
        return None
    return None


def _build_context_artifacts(
    *,
    unit_id: str,
    stage_name: str,
    project_root: Path,
    predecessor_stage: str | None,
) -> None:
    """Build stage-context bundle + (where relevant) precedent cache.

    Failures here must never kill the pipeline — the agent still has the unit
    id and stage name and can produce *something* without the bundle. Log so
    operators notice the degradation in the next status check.
    """
    try:
        build_stage_context(
            unit_id=unit_id,
            stage=stage_name,
            project_root=project_root,
            predecessor_stage=predecessor_stage,
        )
    except Exception as exc:
        logger.warning(
            "controller: build_stage_context failed for unit=%s stage=%s: %s",
            unit_id,
            stage_name,
            exc,
        )

    if stage_name in PRECEDENT_STAGES:
        try:
            build_precedent_cache(
                unit_id=unit_id,
                stage=stage_name,
                project_root=project_root,
            )
        except Exception as exc:
            logger.warning(
                "controller: build_precedent_cache failed for unit=%s stage=%s: %s",
                unit_id,
                stage_name,
                exc,
            )


def _read_marker_safely(unit_id: str, stage_name: str, project_root: Path) -> BuildMarker | None:
    """Return the build marker for the current stage, or None on any problem.

    A marker for a *different* stage is treated as stale debris from an earlier
    run and ignored. A malformed marker is logged but does not halt the loop —
    the controller continues as if no marker were present (graceful
    degradation).
    """
    try:
        marker = read_build_marker(unit_id, project_root)
    except BuildMarkerError as exc:
        logger.warning(
            "controller: malformed build marker for unit=%s — ignoring: %s",
            unit_id,
            exc,
        )
        return None
    if marker is None:
        return None
    if marker.stage != stage_name:
        # Stale marker from a previous stage of this unit; ignore.
        return None
    return marker


def _parse_audit_verdict(audit_artifact: Path) -> AuditVerdict:
    """Read the audit artifact body; return the parsed verdict.

    (v1.0.5) The audit skill's machine-stable verdict line is the routing signal.
    The artifact lives at `.dualpass-state/{unit}/audit-artifact-v{round}.md`. We
    scan it once and return one of four values. `unknown` is the conservative
    fallback when the file is missing, unreadable, or carries no recognizable
    verdict line — the caller halts on `unknown` for operator inspection.
    """
    try:
        body = audit_artifact.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = _AUDIT_VERDICT_RE.search(body)
    if match is None:
        return "unknown"
    value = match.group(1).strip().lower()
    if value == "pass":
        return "pass"
    if value == "needs_remediation":
        return "needs_remediation"
    if value == "architectural_divergence":
        return "architectural_divergence"
    return "unknown"


def _divergence_sidecar_path(project_root: Path, unit_id: str) -> Path:
    """The handoff skill reads this sidecar to switch into the WITH-DEVIATIONS shape."""
    return units_dir(project_root, unit_id) / "divergence-accepted.json"


def _has_divergence_sidecar(project_root: Path, unit_id: str) -> bool:
    return _divergence_sidecar_path(project_root, unit_id).is_file()


def _write_audit_routing_marker(
    project_root: Path,
    unit_id: str,
    *,
    kind: Literal["architectural-divergence", "audit-loop-exhausted"],
    audit_artifact: Path | None,
    audit_iterations: int,
    max_iterations: int,
) -> Path:
    """Drop a stuck-marker the architect can address with `remediate` or `accept-divergence`."""
    marker = state_dir(project_root) / f"{unit_id}-stuck-{kind}.md"
    artifact_line = (
        f"- **audit artifact:** `{audit_artifact}`" if audit_artifact else ""
    )
    if kind == "architectural-divergence":
        explainer = (
            "The audit reported at least one `architectural` finding — the code\n"
            "chose a different design point than the spec ratified. The code author\n"
            "cannot resolve this without your input."
        )
    else:
        explainer = (
            f"The audit returned `NEEDS_REMEDIATION` {audit_iterations} times in a\n"
            f"row without converging (max_audit_iterations={max_iterations}). The\n"
            f"auditor and code author could not agree without architect input."
        )
    marker.write_text(
        f"# Architect intervention required ({kind})\n\n"
        f"- **unit:** `{unit_id}`\n"
        f"- **halted at:** {datetime.now(UTC).isoformat()}\n"
        f"- **audit iterations consumed:** {audit_iterations}/{max_iterations}\n"
        f"{artifact_line}\n\n"
        f"## What this means\n\n"
        f"{explainer}\n\n"
        f"## What to do\n\n"
        f"Inspect the audit FINAL, decide which path to take, then run ONE of:\n\n"
        f"```\n"
        f"# Try again — you believe the divergence is fixable in code:\n"
        f"dualpass remediate --unit {unit_id}\n\n"
        f"# Accept the divergence — ship it documented in the handoff:\n"
        f"dualpass accept-divergence --unit {unit_id} \\\n"
        f"  --rationale \"why this divergence is acceptable\"\n"
        f"```\n\n"
        f"Either command clears this marker and re-enters the pipeline.\n",
        encoding="utf-8",
    )
    return marker


def _write_stuck_marker(
    project_root: Path,
    unit_id: str,
    stage_name: str,
    *,
    kind: Literal["author-stop", "author-escalate"],
    reason: str,
) -> Path:
    """Drop a stuck-* marker next to the unit's other state so operators see it."""
    marker = state_dir(project_root) / f"{unit_id}-stuck-{kind}.md"
    marker.write_text(
        f"# Author halted the run ({kind})\n\n"
        f"- **unit:** `{unit_id}`\n"
        f"- **stage:** `{stage_name}`\n"
        f"- **halted at:** {datetime.now(UTC).isoformat()}\n"
        f"- **reason:** {reason}\n\n"
        f"## What this means\n\n"
        f"The author wrote a build-complete marker requesting a halt before the\n"
        f"reviewer ran. The pipeline stopped cleanly so the operator can\n"
        f"inspect the situation.\n\n"
        f"## What to do\n\n"
        f"1. Inspect the marker at `.dualpass-state/{unit_id}-build-complete.md`\n"
        f"2. Inspect the artifact under `.dualpass-state/{unit_id}/`\n"
        f"3. Address the reason, then re-run with:\n"
        f"   `dualpass run --unit {unit_id} --from-stage {stage_name}`\n",
        encoding="utf-8",
    )
    return marker


def _format_gate_feedback(failures: list[GateResult], stage_name: str, round_number: int) -> str:
    """Render failed-gate diagnostics into a single revision-feedback document."""
    lines = [
        f"# Preflight gate feedback — stage {stage_name!r}, round {round_number}",
        "",
        (
            "One or more preflight gates failed against the artifact you just\n"
            "produced. The reviewer was *not* launched. Address the findings\n"
            "below and re-produce the artifact for the next round."
        ),
        "",
    ]
    for i, result in enumerate(failures, start=1):
        lines.append(f"## Finding {i}")
        lines.append("")
        lines.append(result.diagnostic)
        if result.citations:
            lines.append("")
            lines.append("Citations:")
            for file_path, line_no in result.citations:
                lines.append(f"- {file_path}:{line_no}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _gate_failure_summary(failures: list[GateResult]) -> list[str]:
    """One-line summary per failed gate for inclusion in the event payload."""
    out: list[str] = []
    for result in failures:
        first_line = result.diagnostic.splitlines()[0] if result.diagnostic else ""
        out.append(first_line.strip()[:200])
    return out


def _maybe_lock_final(stage, artifact_path: Path, cfg, run: UnitRun) -> None:
    """If `auto_lock_finals` is true, copy the approved artifact to its FINAL name.

    Emits `stage_finalized` with the final-copy path on success. Failure is
    non-fatal — we log and continue so a filesystem hiccup never wedges the
    pipeline at an otherwise-good approval.
    """
    if not getattr(cfg.project, "auto_lock_finals", False):
        return
    try:
        final_path = _lock_artifact_as_final(artifact_path)
    except OSError as exc:
        logger.warning(
            "controller: auto_lock_finals copy failed for unit=%s stage=%s: %s",
            run.unit_id,
            stage.name,
            exc,
        )
        return
    emit(
        Event(
            "stage_finalized",
            unit=run.unit_id,
            stage=stage.name,
            payload={
                "artifact": str(artifact_path),
                "final": str(final_path),
            },
        ),
        project_root=run.project_root,
    )


def _lock_artifact_as_final(artifact_path: Path) -> Path:
    """Copy `<stage>-artifact-v<N>.md` to `<stage>-v<N>-FINAL.md` next to it.

    The FINAL filename uses the spec's `<stage>-v<N>-FINAL.md` shape (no
    `artifact` infix) so downstream tooling can spot the locked copy without
    parsing version numbers.
    """
    name = artifact_path.name
    # Strip the artifact-* infix if present so we land on `<stage>-v<N>-FINAL.md`.
    # Examples handled:
    #   research-artifact-v1.md → research-v1-FINAL.md
    #   spec-artifact-v3.md     → spec-v3-FINAL.md
    # Strip the `-artifact-` infix if present (e.g. `research-v1.md`), then add
    # the `-FINAL` suffix before the `.md` extension.
    stem = name.replace("-artifact-", "-", 1) if "-artifact-" in name else name
    final_name = (
        stem[: -len(".md")] + "-FINAL.md" if stem.endswith(".md") else stem + "-FINAL"
    )
    target = artifact_path.parent / final_name
    shutil.copy2(artifact_path, target)
    return target


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


def _find_stage_index(stages: tuple, name: str) -> int | None:
    """Return the index of a stage by name, or None if it isn't configured.

    Used by the v1.0.5 audit-routing path to rewind to the code stage on a
    NEEDS_REMEDIATION verdict.
    """
    for i, stage in enumerate(stages):
        if stage.name == name:
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

    predecessor_stage = _predecessor_stage_for(cfg.stages.stages, stage.name)

    for round_number in range(1, max_rounds + 1):
        rounds_used = round_number

        # Build context artifacts at the top of every round so a revision pass
        # picks up the latest predecessor + peer state. Failures are logged but
        # never fatal: the agent retains enough signal from stage name + unit id
        # to proceed in a degraded mode.
        _build_context_artifacts(
            unit_id=run.unit_id,
            stage_name=stage.name,
            project_root=run.project_root,
            predecessor_stage=predecessor_stage,
        )

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

        # Author-driven halt: if the author emitted a build-complete marker
        # naming this stage with a stop/escalate signal, honor it before any
        # reviewer or gate runs. Reviewer rejections never halt the pipeline;
        # this is the only path that does.
        marker = _read_marker_safely(run.unit_id, stage.name, run.project_root)
        if marker is not None and marker.exit_signal in ("stop", "escalate"):
            reason = str(marker.metadata.get("reason", f"author requested {marker.exit_signal}"))
            kind: Literal["author-stop", "author-escalate"] = (
                "author-escalate" if marker.exit_signal == "escalate" else "author-stop"
            )
            stuck_path = _write_stuck_marker(
                run.project_root,
                run.unit_id,
                stage.name,
                kind=kind,
                reason=reason,
            )
            event_payload: dict[str, object] = {
                "round": round_number,
                "reason": reason,
                "exit_signal": marker.exit_signal,
                "stuck_marker": str(stuck_path),
                "blocker_kind": marker.blocker_kind,
            }
            if marker.exit_signal == "escalate":
                event_payload["escalated"] = True
            emit(
                Event(
                    "stage_blocked",
                    unit=run.unit_id,
                    stage=stage.name,
                    payload=event_payload,
                ),
                project_root=run.project_root,
            )
            return StageResult(
                stage=stage.name,
                status="blocked",
                exit_signal="stop",
                artifact_path=author.artifact_path,
                rounds_used=rounds_used,
                blocker_kind=f"author_requested_{marker.exit_signal}",
                blocker_detail=reason,
            )

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
            _maybe_lock_final(stage, author.artifact_path, cfg, run)
            return StageResult(
                stage=stage.name,
                status="complete",
                exit_signal="continue",
                artifact_path=author.artifact_path,
                rounds_used=rounds_used,
            )

        # Preflight gates: run BEFORE any reviewer subprocess so a known-bad
        # artifact (missing frontmatter, stale citations, brittle wording, …)
        # gets caught by a cheap deterministic check instead of burning a
        # reviewer round. A failed gate is treated as an auto-revision: the
        # diagnostics are written to a feedback sidecar that the next-round
        # author can read, and the reviewer is skipped this round.
        if stage.preflight_gates:
            gate_ctx = GateContext(
                unit_id=run.unit_id,
                stage=stage.name,
                project_root=run.project_root,
                artifact_path=author.artifact_path,
            )
            gate_results = run_gates(list(stage.preflight_gates), gate_ctx)
            failed = [r for r in gate_results if not r.passed]
            if failed:
                feedback_path = (
                    udir / f"{stage.name}-gate-feedback-v{round_number}.md"
                )
                feedback_path.write_text(
                    _format_gate_feedback(failed, stage.name, round_number),
                    encoding="utf-8",
                )
                emit(
                    Event(
                        "gate_failed",
                        unit=run.unit_id,
                        stage=stage.name,
                        payload={
                            "round": round_number,
                            "failed_gates": _gate_failure_summary(failed),
                            "feedback_path": str(feedback_path),
                        },
                    ),
                    project_root=run.project_root,
                )
                # Mark this round as no-progress for the circuit breaker
                # bookkeeping (so a stuck author + stuck gate combination still
                # trips eventually) and fall through to the next round without
                # invoking the reviewer.
                emit(
                    Event(
                        "stage_revision_requested",
                        unit=run.unit_id,
                        stage=stage.name,
                        payload={
                            "round": round_number,
                            "verdict": "rejected_by_gate",
                            "no_progress_streak": no_progress_streak,
                            "feedback_path": str(feedback_path),
                        },
                    ),
                    project_root=run.project_root,
                )
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
                                "tripped_by": "gate_failure",
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
                            f"preflight gates kept failing on identical artifacts "
                            f"for {no_progress_streak + 1} consecutive rounds "
                            f"(threshold={breaker_threshold})"
                        ),
                    )
                continue

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
            _maybe_lock_final(stage, author.artifact_path, cfg, run)
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
