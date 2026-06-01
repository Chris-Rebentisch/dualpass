"""dualpass command-line interface.

Top-level commands (all functional as of v1.0.0):
  init         — scaffold a new dualpass project from an example
  doctor       — probe the environment (CLIs, Python, state directory, configs)
  run          — execute a unit through the configured stage chain
  status       — show pipeline state for one unit or all in-flight units
  retro        — open/aggregate retrospectives
  propose-dag  — interactive scoping if your task wants a DAG (v1 stops at scoping)
  watcher      — start/stop/status the background daemons
  config       — validate config files
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from dualpass import __version__

# ──────────────────────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dualpass",
        description=(
            "dualpass — a reliability-first agent harness with cross-vendor "
            "independent review built in.\n\n"
            "One agent ships the work. A different vendor ships the review."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "docs:    https://github.com/Chris-Rebentisch/dualpass/tree/main/docs\n"
            "issues:  https://github.com/Chris-Rebentisch/dualpass/issues"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"dualpass {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>", required=False)

    # init
    p_init = sub.add_parser(
        "init",
        help="Scaffold a new dualpass project from an example",
        description="Copy an example project structure into a target directory.",
    )
    p_init.add_argument("path", help="Target directory for the new project")
    p_init.add_argument(
        "--example",
        default="coding-agent",
        choices=["coding-agent"],
        help="Example project to scaffold from (default: coding-agent)",
    )
    p_init.add_argument(
        "--project-name",
        dest="project_name",
        help="Override the project name written to config/dualpass.json (default: target dir basename)",
    )

    # doctor
    p_doctor = sub.add_parser(
        "doctor",
        help="Probe the environment for agent CLIs, configs, and required services",
        description=(
            "Non-destructive health check. Reports Python version, which agent CLIs "
            "are installed, state-directory writability, and config validity."
        ),
    )
    p_doctor.add_argument(
        "--project",
        default=".",
        help="Project root to probe (default: current directory)",
    )

    # run
    p_run = sub.add_parser(
        "run",
        help="Execute a unit through the configured stage chain",
        description="Launch the controller for one unit. Honors stage breakpoints.",
    )
    p_run.add_argument("--unit", required=True, help="Unit identifier (e.g. my-001)")
    p_run.add_argument(
        "--provider",
        choices=["live", "mock"],
        default="live",
        help="Provider for agent calls (default: live)",
    )
    p_run.add_argument(
        "--from-stage",
        dest="from_stage",
        help="Resume from this stage instead of the start of the chain",
    )
    p_run.add_argument(
        "--ignore-breakpoints",
        dest="ignore_breakpoints",
        action="store_true",
        help="Run through configured breakpoints (default: respect them and pause)",
    )
    p_run.add_argument(
        "--project",
        default=".",
        help="Project root containing the config/ directory (default: current directory)",
    )

    # status
    p_status = sub.add_parser(
        "status",
        help="Show pipeline state for one unit or all in-flight units",
    )
    p_status.add_argument("--unit", help="Show only this unit")
    p_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_status.add_argument(
        "--project",
        default=".",
        help="Project root containing .dualpass-state/ (default: current directory)",
    )

    # retro
    p_retro = sub.add_parser(
        "retro",
        help="Open a unit retrospective, or aggregate retros across a range",
    )
    p_retro.add_argument("--unit", help="Open the retro for a single unit")
    p_retro.add_argument(
        "--range",
        dest="range_spec",
        help="Aggregate retros across a range, e.g. '001..010'",
    )
    p_retro.add_argument(
        "--output",
        help="Output path for the aggregated retro (default: docs/_project/RETROSPECTIVES/<range>.md)",
    )
    p_retro.add_argument(
        "--project",
        default=".",
        help="Project root containing docs/_project/RETROSPECTIVES (default: current directory)",
    )

    # propose-dag
    p_propose = sub.add_parser(
        "propose-dag",
        help="Interactive prompt to scope a DAG if your task needs one (v1 stops at scoping)",
        description=(
            "v1 ships fixed-cycle stages only. If your use case genuinely needs a DAG, "
            "this command walks you through what one would look like — you implement "
            "it outside dualpass. The intent is to surface the design-pressure signal "
            "cleanly rather than silently constrain you."
        ),
    )
    p_propose.add_argument(
        "--project",
        default=".",
        help="Project root where docs/_project/DAG-PROPOSAL.md will be written (default: current directory)",
    )
    p_propose.add_argument(
        "--non-interactive",
        dest="non_interactive",
        action="store_true",
        help="Skip the interactive prompt and use the values from --name/--parallel/--fan-out/--join",
    )
    p_propose.add_argument("--name", default="dag-proposal", help="Slug for the proposal")
    p_propose.add_argument(
        "--parallel",
        default="(describe)",
        help="Which stages could run in parallel",
    )
    p_propose.add_argument(
        "--fan-out",
        dest="fan_out",
        default="(describe)",
        help="Which stage fans out into multiple units",
    )
    p_propose.add_argument(
        "--join",
        default="(describe)",
        help="Where do the parallel paths converge",
    )

    # watcher
    p_watcher = sub.add_parser(
        "watcher",
        help="Manage background watcher daemons",
    )
    p_watcher.add_argument(
        "action",
        choices=["start", "stop", "status", "restart"],
        help="Action to perform",
    )
    p_watcher.add_argument(
        "name",
        nargs="?",
        choices=["research", "prompt", "handoff", "all"],
        default="all",
        help="Which watcher (default: all)",
    )
    p_watcher.add_argument(
        "--provider",
        choices=["live", "mock"],
        default="live",
        help="Provider for triggered runs (default: live)",
    )
    p_watcher.add_argument(
        "--project",
        default=".",
        help="Project root containing .dualpass-state/ (default: current directory)",
    )
    p_watcher.add_argument(
        "--foreground",
        action="store_true",
        help="(start only) Keep the watcher attached to the terminal — useful for debugging",
    )
    p_watcher.add_argument(
        "--poll-interval",
        dest="poll_interval",
        type=int,
        default=None,
        help="(start only) Override the polling interval in seconds (default: 5)",
    )

    # remediate (v1.0.5) — architect "try again in code" disposition
    p_remediate = sub.add_parser(
        "remediate",
        help="(v1.0.5) Clear an architectural-divergence stuck marker and relaunch from code",
        description=(
            "When the audit emits ARCHITECTURAL_DIVERGENCE or the audit-remediation loop\n"
            "exhausts max_audit_iterations, the controller halts and writes a stuck marker.\n"
            "Use this command when you've inspected the marker + audit FINAL and decided the\n"
            "divergence IS fixable in code — the command clears the marker, resets the audit\n"
            "iteration counter for this re-entry, and re-launches the unit from the code stage."
        ),
    )
    p_remediate.add_argument("--unit", required=True, help="Unit identifier")
    p_remediate.add_argument(
        "--from-stage",
        dest="from_stage",
        default="code",
        help="Stage to re-enter (default: code)",
    )
    p_remediate.add_argument(
        "--provider",
        choices=["live", "mock"],
        default="live",
        help="Provider for the relaunched run (default: live)",
    )
    p_remediate.add_argument(
        "--ignore-breakpoints",
        dest="ignore_breakpoints",
        action="store_true",
        help="Walk through configured breakpoints on relaunch",
    )
    p_remediate.add_argument(
        "--project",
        default=".",
        help="Project root containing config/ (default: current directory)",
    )

    # accept-divergence (v1.0.5) — architect "ship this as documented divergence" disposition
    p_accept = sub.add_parser(
        "accept-divergence",
        help="(v1.0.5) Accept an architectural divergence; advance to handoff (WITH-DEVIATIONS shape)",
        description=(
            "When the audit emits ARCHITECTURAL_DIVERGENCE and you've decided the\n"
            "divergence IS the right call, use this command to ship it. The command\n"
            "writes a divergence-accepted.json sidecar (the architect's name + rationale\n"
            "+ the auditor's findings reproduced verbatim), clears the stuck marker, and\n"
            "re-launches the unit from the handoff stage. The handoff drafts the\n"
            "WITH-DEVIATIONS shape — §10a Accepted Divergences carries your rationale\n"
            "and the auditor's findings."
        ),
    )
    p_accept.add_argument("--unit", required=True, help="Unit identifier")
    p_accept.add_argument(
        "--rationale",
        required=True,
        help="Architect rationale (free text; reproduced verbatim in §10a)",
    )
    p_accept.add_argument(
        "--architect",
        default=None,
        help="Architect name/handle (default: $USER)",
    )
    p_accept.add_argument(
        "--provider",
        choices=["live", "mock"],
        default="live",
        help="Provider for the relaunched handoff (default: live)",
    )
    p_accept.add_argument(
        "--ignore-breakpoints",
        dest="ignore_breakpoints",
        action="store_true",
        help="Walk through configured breakpoints on relaunch",
    )
    p_accept.add_argument(
        "--no-run",
        dest="no_run",
        action="store_true",
        help="Write the sidecar + clear the stuck marker, but don't relaunch the handoff stage",
    )
    p_accept.add_argument(
        "--project",
        default=".",
        help="Project root containing config/ (default: current directory)",
    )

    # config
    p_config = sub.add_parser(
        "config",
        help="Validate config files",
    )
    p_config.add_argument(
        "action",
        choices=["validate"],
        help="Action to perform",
    )
    p_config.add_argument(
        "--project",
        default=".",
        help="Project root containing the config/ directory (default: current directory)",
    )

    return parser


# ──────────────────────────────────────────────────────────────────────────────
# Stub handler
# ──────────────────────────────────────────────────────────────────────────────


def _stub(command: str, milestone: str = "") -> int:
    """Defensive fallback for code paths argparse should have rejected.

    Every dispatch arm in `main()` routes to a real handler in v1, and argparse's
    `choices=` constraint blocks unknown subcommands before we get here. This
    function exists only so the dispatcher's "no handler matched" fallback
    produces a clear error if someone wires up a new subcommand and forgets to
    add a matching dispatch arm.
    """
    msg = (
        f"dualpass: internal — '{command}' fell through to the no-handler fallback. "
        f"This is a bug; please file an issue at "
        f"https://github.com/Chris-Rebentisch/dualpass/issues with the exact "
        f"argv you ran."
    )
    if milestone:
        msg += f"  ({milestone})"
    print(msg, file=sys.stderr)
    return 2


# ──────────────────────────────────────────────────────────────────────────────
# Functional commands
# ──────────────────────────────────────────────────────────────────────────────


# CLIs that the harness shells out to. dualpass works without these — doctor
# just reports them as missing so you know what to install before `run`.
_KNOWN_AGENT_CLIS = ("claude", "cursor-agent", "codex")


def _cmd_doctor(project_root: Path) -> int:
    """Probe the environment. Exits 0 if everything looks runnable, 1 otherwise."""
    from dualpass import config as _config
    from dualpass.memory import state_dir

    failed = False

    print(f"dualpass {__version__}")
    print(f"  python: {sys.version.split()[0]}")
    print(f"  project root: {project_root.resolve()}")

    # Agent CLIs.
    print("  agent CLIs:")
    for cli in _KNOWN_AGENT_CLIS:
        found = shutil.which(cli)
        status = found if found else "NOT FOUND"
        print(f"    - {cli}: {status}")

    # State directory.
    try:
        state = state_dir(project_root)
        probe = state / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"  state dir: {state} (writable)")
    except OSError as exc:
        failed = True
        print(f"  state dir: NOT WRITABLE — {exc}", file=sys.stderr)

    # Config validity.
    config_dir = project_root / "config"
    if not config_dir.is_dir():
        print(
            f"  config: no config/ directory at {config_dir} "
            f"(run `dualpass init <path>` to scaffold one)"
        )
    else:
        errors = _config.validate(project_root)
        if errors:
            failed = True
            print(f"  config: {len(errors)} error(s):", file=sys.stderr)
            for err in errors:
                print(f"    - {err.format()}", file=sys.stderr)
        else:
            print("  config: valid")

    if failed:
        print("\ndoctor: FAIL", file=sys.stderr)
        return 1
    print("\ndoctor: OK")
    return 0


def _cmd_init(
    target: Path, *, example: str = "coding-agent", project_name: str | None = None
) -> int:
    """Scaffold a new dualpass project into target. Returns process exit code."""
    from dualpass import _init

    try:
        result = _init.run_init(target, template=example, project_name=project_name)
    except _init.InitError as exc:
        print(f"dualpass init: {exc}", file=sys.stderr)
        return 1
    print(_init.format_next_steps(result))
    return 0


def _cmd_propose_dag(
    *,
    project_root: Path,
    non_interactive: bool,
    name: str,
    parallel: str,
    fan_out: str,
    join: str,
) -> int:
    """Either prompt interactively or take values from flags, then write the sketch."""
    from dualpass import _propose_dag

    if non_interactive:
        answers = _propose_dag.DagAnswers(
            name=name, parallel_stages=parallel, fan_out_stage=fan_out, join_point=join
        )
        path = _propose_dag.write_proposal(project_root, answers)
    else:
        path = _propose_dag.run_interactive(project_root)
    print(f"DAG proposal written: {path}")
    return 0


def _cmd_retro(
    *,
    unit_id: str | None,
    range_spec: str | None,
    output: Path | None,
    project_root: Path,
) -> int:
    """Open a single-unit retro or aggregate a range into a rollup."""
    from dualpass import _retro

    if not unit_id and not range_spec:
        print(
            "dualpass retro: pass either --unit <id> or --range <start..end>",
            file=sys.stderr,
        )
        return 2
    if unit_id and range_spec:
        print(
            "dualpass retro: --unit and --range are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    if unit_id:
        path, created = _retro.open_or_create(project_root, unit_id)
        marker = "created" if created else "exists"
        print(f"retro for {unit_id!r} ({marker}): {path}")
        return 0

    try:
        unit_ids = _retro.parse_range(range_spec)  # type: ignore[arg-type]
    except ValueError as exc:
        print(f"dualpass retro: {exc}", file=sys.stderr)
        return 2

    result = _retro.aggregate(project_root, unit_ids, output)
    print(
        f"rollup written: {result.output}\n"
        f"  included: {len(result.included)} unit(s)\n"
        f"  missing:  {len(result.missing)} unit(s)"
    )
    return 0


def _cmd_status(*, unit_id: str | None, as_json: bool, project_root: Path) -> int:
    """Render status for one unit or all units."""
    from dualpass import observability

    return observability.render_status(project_root, unit_id=unit_id, as_json=as_json)


def _cmd_watcher(
    *,
    action: str,
    name: str,
    project_root: Path,
    foreground: bool = False,
    poll_interval: int | None = None,
    provider: str = "live",
) -> int:
    """Lifecycle management for background watchers."""
    from dualpass import watcher

    if action == "status":
        names: list[watcher.WatcherName] | None
        names = None if name == "all" else [name]  # type: ignore[list-item]
        rows = []
        for n in names or list(watcher.WATCHER_NAMES):
            rows.extend(watcher.status(n, project_root=project_root))  # type: ignore[arg-type]
        if not rows:
            print("(no watcher state recorded)")
        else:
            for row in rows:
                pid_str = str(row.pid) if row.pid else "-"
                print(f"  {row.name:<10} status={row.status:<10} pid={pid_str}")
        return 0

    if action == "stop":
        names = [name] if name != "all" else list(watcher.WATCHER_NAMES)  # type: ignore[assignment]
        stopped_any = False
        for n in names or []:
            if watcher.stop(n, project_root=project_root):  # type: ignore[arg-type]
                print(f"stopped watcher: {n}")
                stopped_any = True
            else:
                print(f"watcher {n}: was not running")
        return 0 if stopped_any else 1

    if action in ("start", "restart"):
        if name == "all":
            # `start all` only makes sense as a convenience; spawn each in turn.
            for n in watcher.WATCHER_NAMES:
                rc = _start_one_watcher(
                    n,
                    project_root,
                    foreground=foreground,
                    poll_interval=poll_interval,
                    provider=provider,
                )
                if rc != 0:
                    return rc
            return 0
        return _start_one_watcher(
            name,
            project_root,
            foreground=foreground,
            poll_interval=poll_interval,
            provider=provider,
        )

    return _stub(f"watcher {action}")


def _start_one_watcher(
    name: str,
    project_root: Path,
    *,
    foreground: bool,
    poll_interval: int | None,
    provider: str,
) -> int:
    """Helper: launch one named watcher; print outcome."""
    from dualpass import watcher

    poll = poll_interval if poll_interval is not None else watcher.DEFAULT_POLL_INTERVAL_SECONDS
    try:
        pid = watcher.start(
            name,  # type: ignore[arg-type]
            provider=provider,
            project_root=project_root,
            poll_interval_seconds=poll,
            foreground=foreground,
        )
    except RuntimeError as exc:
        print(f"dualpass watcher start: {exc}", file=sys.stderr)
        return 1
    if foreground:
        # When foreground=True the start() call blocks until SIGTERM, so by here
        # we've already shut down. Still print so scripts can sanity-check.
        print(f"watcher {name}: exited (pid was {pid})")
    else:
        # In daemon mode the PARENT prints the PID; the child has detached.
        print(f"watcher {name}: launched (pid={pid}, poll={poll}s)")
    return 0


def _cmd_run(
    *,
    unit_id: str,
    provider: str,
    from_stage: str | None,
    ignore_breakpoints: bool,
    project_root: Path,
) -> int:
    """Drive one unit through the configured stage chain."""
    from dualpass import controller

    return controller.run_unit(
        unit_id,
        from_stage=from_stage,
        provider=provider,
        project_root=project_root,
        ignore_breakpoints=ignore_breakpoints,
    )


# ──────────────────────────────────────────────────────────────────────────────
# v1.0.5 — architect dispositions
# ──────────────────────────────────────────────────────────────────────────────


def _clear_stuck_markers(project_root: Path, unit_id: str) -> list[Path]:
    """Delete every architect-intervention stuck marker for this unit.

    There can be at most one in practice (the controller writes either
    `architectural-divergence` OR `audit-loop-exhausted`, never both), but
    we glob defensively so a leftover marker from an earlier run gets
    cleaned up too.
    """
    from dualpass.memory import state_dir

    removed: list[Path] = []
    state = state_dir(project_root)
    for kind in ("architectural-divergence", "audit-loop-exhausted"):
        marker = state / f"{unit_id}-stuck-{kind}.md"
        if marker.is_file():
            marker.unlink()
            removed.append(marker)
    return removed


def _cmd_remediate(
    *,
    unit_id: str,
    from_stage: str,
    provider: str,
    ignore_breakpoints: bool,
    project_root: Path,
) -> int:
    """Architect disposition: try again in code. Clear stuck markers and relaunch."""
    from dualpass import controller

    cleared = _clear_stuck_markers(project_root, unit_id)
    if not cleared:
        print(
            f"dualpass remediate: no architect-intervention stuck marker found for "
            f"unit {unit_id!r}; nothing to clear.",
            file=sys.stderr,
        )
        return 1
    for path in cleared:
        print(f"cleared stuck marker: {path}")

    print(f"relaunching unit {unit_id!r} from stage {from_stage!r}")
    return controller.run_unit(
        unit_id,
        from_stage=from_stage,
        provider=provider,
        project_root=project_root,
        ignore_breakpoints=ignore_breakpoints,
    )


def _read_architectural_findings(project_root: Path, unit_id: str) -> list[dict[str, object]]:
    """Best-effort extraction of `architectural`-severity finding blocks from the audit FINAL.

    The audit author tags each finding with `<!-- severity: architectural -->`. We
    scan the latest audit artifact body, slice out the contiguous block belonging
    to each architectural finding, and pass it to the handoff as opaque text. The
    handoff skill is responsible for re-rendering — we just preserve the source.

    Returns a list of dicts shaped `{"index": int, "body": str}`. Empty list when
    no audit artifact is present or no architectural findings are tagged (which is
    surprising but not fatal; the architect can still accept-divergence and the
    handoff will note the empty list).
    """
    import re as _re

    from dualpass.memory import units_dir

    u = units_dir(project_root, unit_id)
    candidates = sorted(u.glob("audit-artifact-v*.md"))
    if not candidates:
        return []
    audit_body = candidates[-1].read_text(encoding="utf-8")

    findings: list[dict[str, object]] = []
    severity_re = _re.compile(
        r"<!--\s*severity:\s*architectural\s*-->", _re.IGNORECASE
    )
    finding_header_re = _re.compile(
        r"^(#+\s*Finding\s*\d+.*)$", _re.IGNORECASE | _re.MULTILINE
    )

    # Find every architectural-severity tag, walk backward to the nearest
    # Finding header, walk forward to the next Finding header (or EOF). The
    # body in between is what we hand to the handoff verbatim.
    headers: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in finding_header_re.finditer(audit_body)
    ]
    for match in severity_re.finditer(audit_body):
        sev_idx = match.start()
        # Header that opens this finding (largest start <= sev_idx).
        opener: tuple[int, int] | None = None
        for h_start, h_end in headers:
            if h_start <= sev_idx:
                opener = (h_start, h_end)
            else:
                break
        if opener is None:
            continue
        # Closer = next header start strictly after sev_idx, or EOF.
        closer = len(audit_body)
        for h_start, _ in headers:
            if h_start > sev_idx:
                closer = h_start
                break
        body = audit_body[opener[0] : closer].rstrip()
        findings.append({"finding_index": len(findings) + 1, "body": body})
    return findings


def _cmd_accept_divergence(
    *,
    unit_id: str,
    rationale: str,
    architect: str | None,
    provider: str,
    ignore_breakpoints: bool,
    no_run: bool,
    project_root: Path,
) -> int:
    """Architect disposition: accept the divergence; write sidecar + relaunch handoff."""
    import json as _json
    import os
    from datetime import UTC, datetime

    from dualpass import controller
    from dualpass.memory import units_dir

    cleared = _clear_stuck_markers(project_root, unit_id)
    if not cleared:
        print(
            f"dualpass accept-divergence: no architect-intervention stuck marker "
            f"found for unit {unit_id!r}.",
            file=sys.stderr,
        )
        # We still write the sidecar — operator may be running this proactively
        # before the next audit. But signal the unusual state.
    for path in cleared:
        print(f"cleared stuck marker: {path}")

    findings = _read_architectural_findings(project_root, unit_id)

    sidecar_payload = {
        "unit": unit_id,
        "architect": architect or os.environ.get("USER", "unknown"),
        "rationale": rationale,
        "accepted_at": datetime.now(UTC).isoformat(),
        "audit_findings": findings,
    }
    u = units_dir(project_root, unit_id)
    sidecar = u / "divergence-accepted.json"
    sidecar.write_text(_json.dumps(sidecar_payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote divergence-accepted sidecar: {sidecar}")
    print(f"  architectural findings preserved: {len(findings)}")

    if no_run:
        print("(--no-run set — skipping handoff relaunch)")
        return 0

    print(f"relaunching unit {unit_id!r} from stage 'handoff'")
    return controller.run_unit(
        unit_id,
        from_stage="handoff",
        provider=provider,
        project_root=project_root,
        ignore_breakpoints=ignore_breakpoints,
    )


def _cmd_config_validate(project_root: Path) -> int:
    """Validate every config file. Prints each error; exits 0 if valid, 1 otherwise."""
    from dualpass import config as _config

    if not (project_root / "config").is_dir():
        print(
            f"dualpass config: no config/ directory at {project_root.resolve() / 'config'}",
            file=sys.stderr,
        )
        return 1

    errors = _config.validate(project_root)
    if not errors:
        print("config: valid")
        return 0

    print(f"config: {len(errors)} error(s):", file=sys.stderr)
    for err in errors:
        print(f"  - {err.format()}", file=sys.stderr)
    return 1


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "doctor":
        return _cmd_doctor(Path(args.project))
    if args.command == "config" and args.action == "validate":
        return _cmd_config_validate(Path(args.project))
    if args.command == "init":
        return _cmd_init(Path(args.path), example=args.example, project_name=args.project_name)
    if args.command == "run":
        return _cmd_run(
            unit_id=args.unit,
            provider=args.provider,
            from_stage=args.from_stage,
            ignore_breakpoints=args.ignore_breakpoints,
            project_root=Path(args.project),
        )
    if args.command == "status":
        return _cmd_status(unit_id=args.unit, as_json=args.json, project_root=Path(args.project))
    if args.command == "retro":
        return _cmd_retro(
            unit_id=args.unit,
            range_spec=args.range_spec,
            output=Path(args.output) if args.output else None,
            project_root=Path(args.project),
        )
    if args.command == "watcher":
        return _cmd_watcher(
            action=args.action,
            name=args.name,
            project_root=Path(args.project),
            foreground=args.foreground,
            poll_interval=args.poll_interval,
            provider=args.provider,
        )
    if args.command == "propose-dag":
        return _cmd_propose_dag(
            project_root=Path(args.project),
            non_interactive=args.non_interactive,
            name=args.name,
            parallel=args.parallel,
            fan_out=args.fan_out,
            join=args.join,
        )
    if args.command == "remediate":
        return _cmd_remediate(
            unit_id=args.unit,
            from_stage=args.from_stage,
            provider=args.provider,
            ignore_breakpoints=args.ignore_breakpoints,
            project_root=Path(args.project),
        )
    if args.command == "accept-divergence":
        return _cmd_accept_divergence(
            unit_id=args.unit,
            rationale=args.rationale,
            architect=args.architect,
            provider=args.provider,
            ignore_breakpoints=args.ignore_breakpoints,
            no_run=args.no_run,
            project_root=Path(args.project),
        )
    return _stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
