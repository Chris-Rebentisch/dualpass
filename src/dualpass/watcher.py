"""Background watchers — research-complete → outline, prompt-drafts, handoff-finals.

This module owns the lifecycle skeleton: PID-file based start/stop/status, with
stale-PID detection via `os.kill(pid, 0)`. The actual filesystem-watching loop
(scan `.dualpass-state/` for new stage-complete markers and trigger the next
stage) lands in a follow-up milestone.

Why the skeleton ships first: `dualpass watcher status` should not crash, and
the controller wants a clean handle for "do I need to wake a watcher?" before
the loop exists. Splitting lifecycle from semantics also lets us land the
retro-hardened pieces (whitespace-robust PID parsing per BashFAQ/001, lockfile
guard, splits_into frontmatter check) in isolation, where they're testable.
"""

from __future__ import annotations

import errno
import json
import os
import signal
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dualpass.memory import state_dir

WatcherName = Literal["research", "prompt", "handoff"]
WatcherStatus = Literal["running", "stopped", "stale-pid"]
WATCHER_NAMES: tuple[WatcherName, ...] = ("research", "prompt", "handoff")


@dataclass(frozen=True)
class WatcherState:
    """Reported state of one watcher."""

    name: WatcherName
    status: WatcherStatus
    pid: int | None = None
    last_seen_iso: str | None = None
    payload: dict[str, object] = field(default_factory=dict)


# ── PID-file helpers ──────────────────────────────────────────────────────────


def _pid_path(name: WatcherName, project_root: Path) -> Path:
    return state_dir(project_root) / f"watcher-{name}.pid"


def _pid_is_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running.

    Uses `os.kill(pid, 0)` which doesn't actually send a signal — it just
    checks whether the kernel would let us. On macOS / Linux this is the
    canonical liveness probe (per POSIX kill(2)).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but we don't have permission to signal it. Treat
        # as alive — better to refuse to double-start than to clobber state.
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
    # Atomic create — refuse to clobber an existing pidfile.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def _read_pidfile(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Public API ────────────────────────────────────────────────────────────────


def status(
    name: WatcherName | None = None, *, project_root: Path | None = None
) -> list[WatcherState]:
    """Report status of one or all watchers.

    Stale-PID detection: if a pidfile exists but the PID is dead, the watcher
    is reported as `stale-pid` (not `stopped`). The caller can decide whether
    to clean up the stale file.
    """
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
    """Stop a running watcher by SIGTERM. Returns True if a live process was signaled.

    Cleanly handles stale pidfiles: if the recorded PID is dead, the pidfile is
    removed and False is returned (nothing to stop).
    """
    root = (project_root or Path.cwd()).resolve()
    path = _pid_path(name, root)
    record = _read_pidfile(path)
    if record is None:
        return False
    pid = int(record["pid"]) if isinstance(record.get("pid"), int | str) else None
    if pid is None or not _pid_is_alive(pid):
        # Stale pidfile — clean up and report no live process found.
        path.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return False
    # We deliberately do NOT block-wait here — the watcher process is
    # responsible for cleaning up its own pidfile on shutdown. If it doesn't,
    # the next `status` call will report stale-pid and the next `start` will
    # refuse cleanly.
    return True


def start(name: WatcherName, *, provider: str = "live", project_root: Path | None = None) -> int:
    """Launch a watcher daemon. Returns the watcher's PID.

    v0.2.0a0: the actual fs-watching loop is not yet implemented. Calling
    `start()` raises NotImplementedError. This signature is the contract for
    when the loop lands; tests exercise PID-file lifecycle via `status` and
    `stop` against a sentinel PID written by the test harness.
    """
    raise NotImplementedError(
        "watcher.start — the fs-watching loop is not yet implemented. "
        "Lifecycle helpers (status, stop) work. The actual daemon lands later."
    )


def seed_state_before_live(project_root: Path) -> dict[str, list[str]]:
    """Mark existing artifacts as seen before bringing watchers live.

    Retro-hardened lesson: without seeding, starting `--provider live` on a
    stale state file stampedes the entire research/handoff backlog. Returns a
    report of what was seeded per watcher.
    """
    raise NotImplementedError("watcher.seed_state_before_live — landing with the watcher loop.")


# ── Test/admin hook — only public so the harness can write fake pidfiles ─────


def _write_synthetic_pidfile(
    name: WatcherName, pid: int, *, project_root: Path, provider: str = "mock"
) -> Path:
    """Write a pidfile pointing at `pid` (without launching anything).

    This is intentionally `_underscore`-prefixed because it's not a real
    lifecycle entry point — only the start() daemon should normally create
    pidfiles. Tests use it to exercise `status` and `stop` paths.
    """
    path = _pid_path(name, project_root)
    _write_pidfile(path, pid, name=name, provider=provider)
    return path
