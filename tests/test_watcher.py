"""Tests for the watcher PID-file lifecycle (v0.2.0a1)."""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from pathlib import Path

from dualpass import watcher
from dualpass.cli import main

# ── Library API ───────────────────────────────────────────────────────────────


def test_status_reports_stopped_when_no_pidfile(tmp_path: Path) -> None:
    rows = watcher.status(project_root=tmp_path)
    assert {r.name for r in rows} == {"research", "prompt", "handoff"}
    assert all(r.status == "stopped" for r in rows)
    assert all(r.pid is None for r in rows)


def test_status_reports_running_when_pid_is_alive(tmp_path: Path) -> None:
    """Use our own PID as the 'live' sentinel — guaranteed running."""
    watcher._write_synthetic_pidfile("research", os.getpid(), project_root=tmp_path)
    rows = watcher.status("research", project_root=tmp_path)
    assert len(rows) == 1
    assert rows[0].name == "research"
    assert rows[0].status == "running"
    assert rows[0].pid == os.getpid()


def test_status_reports_stale_pid_when_process_is_dead(tmp_path: Path) -> None:
    """A PID that doesn't exist should report stale-pid, not stopped."""
    dead_pid = _find_dead_pid()
    watcher._write_synthetic_pidfile("prompt", dead_pid, project_root=tmp_path)
    rows = watcher.status("prompt", project_root=tmp_path)
    assert rows[0].status == "stale-pid"
    assert rows[0].pid == dead_pid


def test_stop_removes_stale_pidfile_and_returns_false(tmp_path: Path) -> None:
    dead_pid = _find_dead_pid()
    watcher._write_synthetic_pidfile("handoff", dead_pid, project_root=tmp_path)
    assert watcher.stop("handoff", project_root=tmp_path) is False
    # Pidfile should be cleaned up.
    rows = watcher.status("handoff", project_root=tmp_path)
    assert rows[0].status == "stopped"


def test_stop_returns_false_when_no_pidfile(tmp_path: Path) -> None:
    assert watcher.stop("research", project_root=tmp_path) is False


def test_stop_sends_sigterm_to_running_pid(tmp_path: Path) -> None:
    """Spawn a child that sleeps, write a pidfile pointing at it, stop it.

    Uses subprocess (not multiprocessing) so we don't have to deal with
    pickling restrictions on local functions.
    """
    import subprocess
    import sys

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        watcher._write_synthetic_pidfile("research", proc.pid, project_root=tmp_path)
        assert watcher.status("research", project_root=tmp_path)[0].status == "running"
        assert watcher.stop("research", project_root=tmp_path) is True
        # Give the kernel a moment to deliver SIGTERM.
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            raise AssertionError("SIGTERM did not stop the child within 5s") from exc
        assert proc.returncode != 0  # killed by signal, not normal exit
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# Note: as of v1, watcher.start() is fully implemented. See test_watcher_loop.py
# for tests that exercise the daemon lifecycle end-to-end.


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_watcher_status_prints_all_three_watchers(tmp_path: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["watcher", "status", "--project", str(tmp_path)])
    assert rc == 0
    body = out.getvalue()
    for n in ("research", "prompt", "handoff"):
        assert n in body
    assert body.count("status=stopped") == 3


def test_cli_watcher_status_single_target(tmp_path: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["watcher", "status", "research", "--project", str(tmp_path)])
    assert rc == 0
    assert "research" in out.getvalue()
    # The other watchers should NOT appear when a specific one was requested.
    assert "prompt" not in out.getvalue()


def test_cli_watcher_stop_with_no_running_watchers_exits_one(tmp_path: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["watcher", "stop", "research", "--project", str(tmp_path)])
    assert rc == 1
    assert "was not running" in out.getvalue()


# Note: `watcher start` is no longer a stub. End-to-end coverage lives in
# tests/test_watcher_loop.py::test_start_foreground_writes_pidfile_and_responds_to_stop.


# ── Helpers ──────────────────────────────────────────────────────────────────


def _find_dead_pid() -> int:
    """Find a PID that's almost certainly not in use right now."""
    # POSIX PIDs cap at PID_MAX. 4194303 is Linux's default ceiling; macOS goes
    # lower. Either way, a PID this high is unlikely to be live in a test
    # process. We also confirm with os.kill(pid, 0).
    for candidate in (4194303, 999999, 65533, 65534):
        try:
            os.kill(candidate, 0)
        except (ProcessLookupError, OSError):
            return candidate
    raise RuntimeError("no obviously-dead PID available for the test")
