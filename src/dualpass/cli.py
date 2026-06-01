"""dualpass command-line interface.

Top-level commands:
  init         — scaffold a new dualpass project from an example
  doctor       — probe the environment (CLIs, Python, state directory, configs)
  run          — execute a unit through the configured stage chain
  status       — show pipeline state for one unit or all in-flight units
  retro        — open/aggregate retrospectives
  propose-dag  — interactive scoping if your task wants a DAG (v1 stops here; impl is yours)
  watcher      — start/stop/status the background daemons
  config       — validate config files

v0.1.0a1 status:
  Functional: `--version`, `--help`, `doctor`, `config validate`.
  Stub:       `init`, `run`, `status`, `retro`, `propose-dag`, `watcher`.

Stub commands emit a structured 'not yet implemented' message with a milestone
pointer and exit 2.
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

    # propose-dag
    sub.add_parser(
        "propose-dag",
        help="Interactive prompt to scope a DAG if your task needs one (v1 stops at scoping)",
        description=(
            "v1 ships fixed-cycle stages only. If your use case genuinely needs a DAG, "
            "this command walks you through what one would look like — you implement "
            "it outside dualpass. The intent is to surface the design-pressure signal "
            "cleanly rather than silently constrain you."
        ),
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


_CONTROLLER_MILESTONE = "v0.2.0 (controller, stage runner, mock provider)"
_WATCHER_MILESTONE = "v0.3.0 (watchers + state seeding)"


def _stub(command: str, milestone: str = _CONTROLLER_MILESTONE) -> int:
    msg = (
        f"dualpass: '{command}' is not yet implemented.\n"
        f"  v{__version__} ships scaffolding only. This command lands in {milestone}.\n"
        f"  Track progress: https://github.com/Chris-Rebentisch/dualpass/blob/main/CHANGELOG.md"
    )
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


def _cmd_watcher(*, action: str, name: str, project_root: Path) -> int:
    """Lifecycle management for background watchers (status/stop work; start stubbed)."""
    from dualpass import watcher

    if action == "status":
        names: list[watcher.WatcherName] | None
        names = None if name == "all" else [name]  # type: ignore[list-item]
        rows = []
        for n in names or list(watcher.WATCHER_NAMES):
            rows.extend(watcher.status(n, project_root=project_root))  # type: ignore[arg-type]
        # Compact human-readable table.
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

    if action == "start" or action == "restart":
        return _stub(f"watcher {action}", _WATCHER_MILESTONE)

    return _stub(f"watcher {action}", _WATCHER_MILESTONE)


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
    if args.command == "watcher":
        return _cmd_watcher(action=args.action, name=args.name, project_root=Path(args.project))
    return _stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
