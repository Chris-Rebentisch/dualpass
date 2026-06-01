"""Tests for the watcher fs-polling loop (watch_once + seed + start).

The daemon mode (double-fork → enter loop forever) is exercised end-to-end
in `test_watcher.py` via PID-file lifecycle assertions. THIS module tests the
synchronous, testable building blocks: `watch_once`, `seed_state_before_live`,
and the marker discipline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dualpass import _init, watcher
from dualpass.memory import lock_path, state_dir
from dualpass.observability import Event, emit


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    _init.run_init(target)
    return target


# ── Helper: simulate a paused unit ──────────────────────────────────────────


def _pause_unit_at(unit_id: str, stage: str, project_root: Path) -> None:
    """Make `unit_id` look like a run that paused at `stage`'s breakpoint."""
    emit(Event("unit_started", unit=unit_id), project_root=project_root)
    emit(Event("lockfile_acquired", unit=unit_id), project_root=project_root)
    emit(Event("breakpoint_hit", unit=unit_id, stage=stage), project_root=project_root)
    emit(Event("lockfile_released", unit=unit_id), project_root=project_root)


def _drop_approval(unit_id: str, stage: str, project_root: Path) -> Path:
    """Drop the approval marker as the operator would after reviewing."""
    p = state_dir(project_root) / f"{unit_id}-approved-{stage}.md"
    p.write_text("approved by operator at $(date)\n")
    return p


# ── watch_once: happy path ───────────────────────────────────────────────────


def test_watch_once_triggers_paused_unit_with_approval(scaffolded_project: Path) -> None:
    """Unit paused at research + approval marker present → watcher triggers it."""
    _pause_unit_at("demo-001", "research", scaffolded_project)
    _drop_approval("demo-001", "research", scaffolded_project)

    results = watcher.watch_once("research", scaffolded_project, dry_run=True)
    assert len(results) == 1
    assert results[0].unit_id == "demo-001"
    assert results[0].stage == "research"
    # Handled marker was written (dry_run still writes it — that's the contract).
    assert results[0].handled_marker_path.is_file()


def test_watch_once_skips_unit_not_paused(scaffolded_project: Path) -> None:
    """An approval marker for a unit that isn't paused at this stage is ignored."""
    # Emit only a `unit_started` — unit is in-flight, not paused.
    emit(Event("unit_started", unit="demo-fresh"), project_root=scaffolded_project)
    _drop_approval("demo-fresh", "research", scaffolded_project)
    results = watcher.watch_once("research", scaffolded_project, dry_run=True)
    assert results == []


def test_watch_once_skips_unit_paused_at_different_stage(scaffolded_project: Path) -> None:
    """research-watcher must ignore units paused at prompt or handoff."""
    _pause_unit_at("demo-other", "prompt", scaffolded_project)
    _drop_approval("demo-other", "prompt", scaffolded_project)
    # research watcher: no triggers
    assert watcher.watch_once("research", scaffolded_project, dry_run=True) == []
    # prompt watcher: triggers
    assert len(watcher.watch_once("prompt", scaffolded_project, dry_run=True)) == 1


def test_watch_once_skips_already_handled(scaffolded_project: Path) -> None:
    """If <unit>-handled-<stage>.md already exists, skip."""
    _pause_unit_at("demo-handled", "research", scaffolded_project)
    _drop_approval("demo-handled", "research", scaffolded_project)
    # Pre-mark handled.
    (state_dir(scaffolded_project) / "demo-handled-handled-research.md").write_text("seen")
    results = watcher.watch_once("research", scaffolded_project, dry_run=True)
    assert results == []


def test_watch_once_skips_locked_unit(scaffolded_project: Path) -> None:
    """If the unit currently has a lockfile (another run in flight), skip."""
    _pause_unit_at("demo-locked", "research", scaffolded_project)
    _drop_approval("demo-locked", "research", scaffolded_project)
    # Plant a lockfile.
    lock_path("demo-locked", scaffolded_project).write_text(
        json.dumps({"unit": "demo-locked", "pid": 99999})
    )
    results = watcher.watch_once("research", scaffolded_project, dry_run=True)
    assert results == []


def test_watch_once_idempotent_across_calls(scaffolded_project: Path) -> None:
    """Second call should NOT re-trigger (the handled marker prevents it)."""
    _pause_unit_at("demo-once", "research", scaffolded_project)
    _drop_approval("demo-once", "research", scaffolded_project)

    first = watcher.watch_once("research", scaffolded_project, dry_run=True)
    second = watcher.watch_once("research", scaffolded_project, dry_run=True)
    assert len(first) == 1
    assert second == []


# ── seed_state_before_live ──────────────────────────────────────────────────


def test_seed_marks_existing_approvals_as_handled(scaffolded_project: Path) -> None:
    """seed_state should NOT trigger any runs — it should just write handled-markers."""
    _pause_unit_at("demo-seed", "research", scaffolded_project)
    _drop_approval("demo-seed", "research", scaffolded_project)

    report = watcher.seed_state_before_live("research", scaffolded_project)
    assert report["seeded"] == ["demo-seed"]
    # The handled-marker exists.
    handled = state_dir(scaffolded_project) / "demo-seed-handled-research.md"
    assert handled.is_file()
    assert "seeded" in handled.read_text()

    # After seeding, watch_once should skip the seeded unit.
    results = watcher.watch_once("research", scaffolded_project, dry_run=True)
    assert results == []


def test_seed_record_file_written(scaffolded_project: Path) -> None:
    """A summary record should be written so we can diagnose the seed pass."""
    _pause_unit_at("demo-a", "prompt", scaffolded_project)
    _drop_approval("demo-a", "prompt", scaffolded_project)

    watcher.seed_state_before_live("prompt", scaffolded_project)
    record = state_dir(scaffolded_project) / "watcher-prompt.seed.json"
    assert record.is_file()
    parsed = json.loads(record.read_text())
    assert parsed["watcher"] == "prompt"
    assert parsed["seeded_unit_ids"] == ["demo-a"]


# ── start with foreground=True + immediate stop (smoke for the daemon loop) ──


def test_start_foreground_writes_pidfile_and_responds_to_stop(
    scaffolded_project: Path,
) -> None:
    """Start the watcher in foreground in a subprocess; signal it to stop; confirm cleanup."""
    import os
    import subprocess
    import sys
    import time

    # Pass PYTHONPATH explicitly so the subprocess can find dualpass even when
    # the .pth file is hidden by macOS environment quirks (CI/Linux is fine).
    env = os.environ.copy()
    src_dir = Path(__file__).resolve().parent.parent / "src"
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else str(src_dir)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; from dualpass import watcher; "
            f"watcher.start('research', project_root=Path({str(scaffolded_project)!r}), "
            f"poll_interval_seconds=1, foreground=True)",
        ],
        env=env,
    )
    try:
        # Wait up to 2s for the pidfile to appear.
        pidpath = state_dir(scaffolded_project) / "watcher-research.pid"
        deadline = time.time() + 3
        while not pidpath.is_file() and time.time() < deadline:
            time.sleep(0.1)
        assert pidpath.is_file(), "watcher did not write a pidfile within 3s"

        # status should report running
        rows = watcher.status("research", project_root=scaffolded_project)
        assert rows[0].status == "running"

        # stop it
        assert watcher.stop("research", project_root=scaffolded_project) is True

        # wait for process to actually exit
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise AssertionError("watcher did not exit within 10s of SIGTERM") from None

        # Pidfile should be cleaned up by the daemon's finally block.
        assert not pidpath.is_file()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_start_refuses_when_already_running(scaffolded_project: Path) -> None:
    """If a live pidfile already exists, start() should raise."""
    import os as _os

    watcher._write_synthetic_pidfile("research", _os.getpid(), project_root=scaffolded_project)
    with pytest.raises(RuntimeError, match="already running"):
        watcher.start("research", project_root=scaffolded_project, foreground=True)
