"""Background watchers — auto-resume paused units when an approval marker appears.

Each named watcher (`research` / `prompt` / `handoff`) polls `.dualpass-state/`
for units paused at THAT stage's breakpoint. When the watcher sees an approval
marker file (`<unit>-approved-<stage>.md`) sitting next to the events log, AND
no pipeline lockfile is currently held, it spawns:

    dualpass run --unit <unit-id> --from-stage <stage-name> --ignore-breakpoints

so the controller resumes the run. The marker is moved to `<unit>-handled-<stage>.md`
so we don't re-trigger on the next poll.

The design is intentionally simple:
  - Polling, not inotify — portable to macOS and CI runners without extra deps.
  - One daemon per watcher name (independent lifecycles).
  - Atomic PID-file create via `O_CREAT | O_EXCL` (already shared with the rest
    of the harness).
  - "Acted-on" markers prevent double-trigger across poll cycles.

State seeding (`seed_state_before_live`) is called on first start so the watcher
doesn't stampede the entire historical backlog of paused units when it comes
online. Without seeding, a freshly-started watcher would see every paused unit
in the project's history and try to resume them all at once.

Lifecycle helpers (`status`, `stop`, `_pid_is_alive`) are reused from the
v0.2.0a1 skeleton.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dualpass.memory import lock_present, state_dir
from dualpass.observability import read_events

logger = logging.getLogger(__name__)

WatcherName = Literal["research", "prompt", "handoff"]
WatcherStatus = Literal["running", "stopped", "stale-pid"]
WATCHER_NAMES: tuple[WatcherName, ...] = ("research", "prompt", "handoff")

# Default poll interval. Long enough to be cheap, short enough that users
# don't feel they're waiting forever for the watcher to react.
DEFAULT_POLL_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class WatcherState:
    """Reported state of one watcher."""

    name: WatcherName
    status: WatcherStatus
    pid: int | None = None
    last_seen_iso: str | None = None
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TriggerResult:
    """One unit that the watcher acted on during a poll cycle."""

    unit_id: str
    stage: str
    marker_path: Path
    handled_marker_path: Path
    spawned_pid: int | None  # None if dry-run or spawn failed


# ── PID-file helpers (carried over from v0.2.0a1) ────────────────────────────


def _pid_path(name: WatcherName, project_root: Path) -> Path:
    return state_dir(project_root) / f"watcher-{name}.pid"


def _seed_path(name: WatcherName, project_root: Path) -> Path:
    return state_dir(project_root) / f"watcher-{name}.seed.json"


def _log_path(name: WatcherName, project_root: Path) -> Path:
    return state_dir(project_root) / f"watcher-{name}.log"


def _pid_is_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` liveness probe — same as memory.acquire_lock."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _write_pidfile(path: Path, pid: int, *, name: WatcherName, provider: str) -> None:
    payload = json.dumps(
        {
            "watcher": name,
            "pid": pid,
            "provider": provider,
            "started_at": datetime.now(UTC).isoformat(),
        },
        sort_keys=True,
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def _read_pidfile(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Status + stop (unchanged from v0.2.0a1) ──────────────────────────────────


def status(
    name: WatcherName | None = None, *, project_root: Path | None = None
) -> list[WatcherState]:
    """Report status of one or all watchers."""
    root = (project_root or Path.cwd()).resolve()
    names: tuple[WatcherName, ...] = (name,) if name is not None else WATCHER_NAMES
    out: list[WatcherState] = []
    for n in names:
        path = _pid_path(n, root)
        record = _read_pidfile(path)
        if record is None:
            out.append(WatcherState(name=n, status="stopped"))
            continue
        pid_value = record.get("pid")
        pid = int(pid_value) if isinstance(pid_value, int | str) else None
        if pid is None or not _pid_is_alive(pid):
            out.append(
                WatcherState(
                    name=n,
                    status="stale-pid",
                    pid=pid,
                    last_seen_iso=str(record.get("started_at"))
                    if record.get("started_at")
                    else None,
                    payload=record,
                )
            )
            continue
        out.append(
            WatcherState(
                name=n,
                status="running",
                pid=pid,
                last_seen_iso=str(record.get("started_at")) if record.get("started_at") else None,
                payload=record,
            )
        )
    return out


def stop(name: WatcherName, *, project_root: Path | None = None) -> bool:
    """SIGTERM a running watcher. Returns True if a live process was signaled."""
    root = (project_root or Path.cwd()).resolve()
    path = _pid_path(name, root)
    record = _read_pidfile(path)
    if record is None:
        return False
    pid = int(record["pid"]) if isinstance(record.get("pid"), int | str) else None
    if pid is None or not _pid_is_alive(pid):
        path.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return False
    return True


# ── Marker discovery ─────────────────────────────────────────────────────────


def _is_paused_at(unit_id: str, stage: WatcherName, project_root: Path) -> bool:
    """True iff the unit's most recent semantic event is a breakpoint_hit at this stage."""
    events = read_events(unit_id, project_root)
    if not events:
        return False
    _LOCK_EVENTS = {"lockfile_acquired", "lockfile_released", "lockfile_conflict"}
    for e in reversed(events):
        if e.type in _LOCK_EVENTS:
            continue
        return e.type == "breakpoint_hit" and e.stage == stage
    return False


def _approval_marker(unit_id: str, stage: WatcherName, project_root: Path) -> Path:
    return state_dir(project_root) / f"{unit_id}-approved-{stage}.md"


def _handled_marker(unit_id: str, stage: WatcherName, project_root: Path) -> Path:
    return state_dir(project_root) / f"{unit_id}-handled-{stage}.md"


def _seen_unit_ids(project_root: Path) -> list[str]:
    """All units that have an events log under `.dualpass-state/`."""
    sdir = state_dir(project_root)
    suffix = "-events.jsonl"
    return sorted(
        p.name[: -len(suffix)] for p in sdir.iterdir() if p.is_file() and p.name.endswith(suffix)
    )


# ── Seeding ──────────────────────────────────────────────────────────────────


def seed_state_before_live(name: WatcherName, project_root: Path) -> dict[str, list[str]]:
    """Mark every existing approval marker as already-seen so we don't stampede.

    Without seeding, starting a watcher against a project with a backlog of
    accumulated approval markers would trigger a flurry of `dualpass run`
    invocations all at once. Seeding writes an empty `<unit>-handled-<stage>.md`
    for every approval marker that already exists, so the polling loop sees
    them as "already-acted-on" and skips.

    Returns a {"seeded": [unit_ids]} report.
    """
    root = project_root.resolve()
    seeded: list[str] = []
    for uid in _seen_unit_ids(root):
        marker = _approval_marker(uid, name, root)
        if not marker.is_file():
            continue
        handled = _handled_marker(uid, name, root)
        if not handled.is_file():
            handled.write_text(
                f"seeded at {datetime.now(UTC).isoformat()} — pre-existing approval skipped\n",
                encoding="utf-8",
            )
            seeded.append(uid)

    seed_record = _seed_path(name, root)
    seed_record.write_text(
        json.dumps(
            {
                "watcher": name,
                "seeded_at": datetime.now(UTC).isoformat(),
                "seeded_unit_ids": seeded,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {"seeded": seeded}


# ── Poll cycle (testable, synchronous) ───────────────────────────────────────


def watch_once(
    name: WatcherName,
    project_root: Path,
    *,
    dualpass_argv: list[str] | None = None,
    dry_run: bool = False,
) -> list[TriggerResult]:
    """One scan-and-trigger pass. Used by the daemon loop AND by tests directly.

    For each unit:
      1. Paused at THIS stage's breakpoint? (latest semantic event = breakpoint_hit @ <stage>)
      2. Approval marker present? (`<unit>-approved-<stage>.md`)
      3. Already handled? (`<unit>-handled-<stage>.md` exists) — skip
      4. Lockfile held? — skip (another run is in flight)
      5. Otherwise: write the handled-marker FIRST (so we don't re-trigger on a
         retry mid-spawn), then spawn `dualpass run --unit <id> --from-stage
         <stage> --ignore-breakpoints` via subprocess.Popen.
    """
    root = project_root.resolve()
    results: list[TriggerResult] = []

    for uid in _seen_unit_ids(root):
        marker = _approval_marker(uid, name, root)
        if not marker.is_file():
            continue
        handled = _handled_marker(uid, name, root)
        if handled.is_file():
            continue
        if not _is_paused_at(uid, name, root):
            continue
        if lock_present(uid, root):
            logger.info("watcher %s: skipping %s (lockfile present)", name, uid)
            continue

        # Atomically record that we're handling it. Writing BEFORE spawn means a
        # crash mid-spawn won't re-trigger on the next poll.
        handled.write_text(
            f"handled at {datetime.now(UTC).isoformat()} by watcher {name!r}\n",
            encoding="utf-8",
        )

        if dry_run:
            results.append(
                TriggerResult(
                    unit_id=uid,
                    stage=name,
                    marker_path=marker,
                    handled_marker_path=handled,
                    spawned_pid=None,
                )
            )
            continue

        argv = dualpass_argv or [
            sys.executable,
            "-m",
            "dualpass",
            "run",
            "--unit",
            uid,
            "--from-stage",
            name,
            "--ignore-breakpoints",
            "--project",
            str(root),
        ]
        try:
            proc = subprocess.Popen(argv, start_new_session=True)
            logger.info("watcher %s: spawned dualpass run for %s (pid=%d)", name, uid, proc.pid)
            results.append(
                TriggerResult(
                    unit_id=uid,
                    stage=name,
                    marker_path=marker,
                    handled_marker_path=handled,
                    spawned_pid=proc.pid,
                )
            )
        except (OSError, ValueError) as exc:
            logger.error("watcher %s: spawn failed for %s: %s", name, uid, exc)
            handled.unlink(missing_ok=True)  # allow retry next poll
    return results


# ── Daemonize + main loop ────────────────────────────────────────────────────


def _daemonize_double_fork() -> None:
    """Detach from terminal. Standard double-fork pattern."""
    # First fork — parent exits, child becomes orphan.
    if os.fork() > 0:
        os._exit(0)
    # New session.
    os.setsid()
    # Second fork — prevents reacquiring a controlling terminal.
    if os.fork() > 0:
        os._exit(0)
    # Redirect stdio to /dev/null so logging doesn't write to a closed terminal.
    os.chdir("/")
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 0)
    os.dup2(null, 1)
    os.dup2(null, 2)
    os.close(null)


def _run_loop(
    name: WatcherName,
    project_root: Path,
    *,
    poll_interval_seconds: int,
    log_path: Path,
) -> None:
    """The actual daemon body. Polls forever; exits cleanly on SIGTERM."""
    # Configure file-based logging (the daemon has no stdout/stderr).
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    stop_flag = {"stop": False}

    def _on_term(_signum: int, _frame) -> None:
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    logger.info("watcher %s: started (pid=%d)", name, os.getpid())
    while not stop_flag["stop"]:
        try:
            triggered = watch_once(name, project_root)
            if triggered:
                logger.info(
                    "watcher %s: triggered %d unit(s) — %s",
                    name,
                    len(triggered),
                    ", ".join(t.unit_id for t in triggered),
                )
        except Exception as exc:  # noqa: BLE001 — keep the daemon alive
            logger.exception("watcher %s: poll cycle failed: %s", name, exc)
        # Sleep in small slices so SIGTERM has low latency.
        slept = 0
        while slept < poll_interval_seconds and not stop_flag["stop"]:
            time.sleep(1)
            slept += 1

    # Clean shutdown.
    logger.info("watcher %s: stopping (pid=%d)", name, os.getpid())
    _pid_path(name, project_root).unlink(missing_ok=True)


def start(
    name: WatcherName,
    *,
    provider: str = "live",
    project_root: Path | None = None,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    foreground: bool = False,
) -> int:
    """Launch a watcher daemon. Returns the watcher's PID.

    foreground=True keeps the process attached to the terminal (useful for
    debugging and CI). The default behavior double-forks into the background.
    """
    root = (project_root or Path.cwd()).resolve()
    pidpath = _pid_path(name, root)

    # Refuse to start if a live watcher already holds the PID file.
    existing = _read_pidfile(pidpath)
    if existing:
        existing_pid = int(existing["pid"]) if isinstance(existing.get("pid"), int | str) else None
        if existing_pid and _pid_is_alive(existing_pid):
            raise RuntimeError(
                f"watcher {name!r} already running (pid={existing_pid}); "
                f"stop it first with `dualpass watcher stop {name}`"
            )
        # Stale → clean up so we can claim the slot.
        pidpath.unlink(missing_ok=True)

    # Seed BEFORE going live so we don't stampede the backlog.
    seed_state_before_live(name, root)

    log_path = _log_path(name, root)

    if not foreground:
        _daemonize_double_fork()

    _write_pidfile(pidpath, os.getpid(), name=name, provider=provider)
    try:
        _run_loop(
            name,
            root,
            poll_interval_seconds=poll_interval_seconds,
            log_path=log_path,
        )
    finally:
        pidpath.unlink(missing_ok=True)
    return os.getpid()


# ── Test/admin hook (kept stable for the v0.2.0a1 watcher tests) ─────────────


def _write_synthetic_pidfile(
    name: WatcherName, pid: int, *, project_root: Path, provider: str = "mock"
) -> Path:
    """Write a pidfile pointing at `pid` (without launching anything)."""
    path = _pid_path(name, project_root)
    _write_pidfile(path, pid, name=name, provider=provider)
    return path
