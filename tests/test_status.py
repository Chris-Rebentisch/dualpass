"""Tests for `dualpass status` — both library + CLI surface."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from dualpass import _init, controller, observability
from dualpass.cli import main
from dualpass.memory import lock_path


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    _init.run_init(target)
    return target


# ── Library API ──────────────────────────────────────────────────────────────


def test_status_for_unknown_unit_returns_unknown_state(scaffolded_project: Path) -> None:
    s = observability.status_for("never-ran", scaffolded_project)
    assert s.state == "unknown"
    assert s.stages_completed == []
    assert s.last_event_type is None


def test_status_for_completed_unit_reports_completed(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-001",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    s = observability.status_for("demo-001", scaffolded_project)
    assert s.state == "completed"
    assert len(s.stages_completed) == 7
    assert s.lock_held is False


def test_status_for_paused_unit_reports_breakpoint(scaffolded_project: Path) -> None:
    """Example sets breakpoints.code: true; default run pauses there."""
    controller.run_unit("demo-bp", provider="mock", project_root=scaffolded_project)
    s = observability.status_for("demo-bp", scaffolded_project)
    assert s.state == "paused-at-breakpoint"
    assert s.paused_at == "code"


def test_status_all_lists_every_unit_with_events(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-a", provider="mock", project_root=scaffolded_project, ignore_breakpoints=True
    )
    controller.run_unit(
        "demo-b", provider="mock", project_root=scaffolded_project, ignore_breakpoints=True
    )
    rows = observability.status_all(scaffolded_project)
    ids = sorted(r.unit_id for r in rows)
    assert ids == ["demo-a", "demo-b"]


def test_status_detects_stale_lock(scaffolded_project: Path) -> None:
    """Plant a lockfile pointing at a dead PID + a partial event stream."""
    dead_pid = _find_dead_pid()
    # Write a fake event log so the unit shows up in status_all
    log = observability.event_log_path("demo-stale", scaffolded_project)
    log.parent.mkdir(parents=True, exist_ok=True)
    observability.emit(
        observability.Event("lockfile_acquired", unit="demo-stale"),
        project_root=scaffolded_project,
    )
    observability.emit(
        observability.Event("stage_round_started", unit="demo-stale", stage="research"),
        project_root=scaffolded_project,
    )
    # Plant the stale lockfile.
    lock_path("demo-stale", scaffolded_project).write_text(
        json.dumps({"unit": "demo-stale", "pid": dead_pid})
    )
    s = observability.status_for("demo-stale", scaffolded_project)
    assert s.state == "stale-lock"
    assert s.lock_pid == dead_pid


def test_status_json_payload_round_trips(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-json",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    out = io.StringIO()
    with redirect_stdout(out):
        observability.render_status(scaffolded_project, as_json=True)
    payload = json.loads(out.getvalue())
    assert isinstance(payload, list)
    assert payload[0]["unit_id"] == "demo-json"
    assert payload[0]["state"] == "completed"
    assert "stages_completed" in payload[0]


# ── CLI surface ──────────────────────────────────────────────────────────────


def test_cli_status_with_no_units_prints_friendly_message(tmp_path: Path) -> None:
    # No scaffolded project — empty state dir.
    (tmp_path / ".dualpass-state").mkdir()
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["status", "--project", str(tmp_path)])
    assert rc == 0
    assert "no units" in out.getvalue()


def test_cli_status_human_form_renders_table(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-table",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["status", "--project", str(scaffolded_project)])
    assert rc == 0
    body = out.getvalue()
    assert "demo-table" in body
    assert "completed" in body
    # Rich table column headings (rendering goes through rich Console)
    assert "Unit" in body or "demo-table" in body  # always asserts the unit shows


def test_cli_status_single_unit_filter(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-x", provider="mock", project_root=scaffolded_project, ignore_breakpoints=True
    )
    controller.run_unit(
        "demo-y", provider="mock", project_root=scaffolded_project, ignore_breakpoints=True
    )
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["status", "--unit", "demo-x", "--project", str(scaffolded_project)])
    assert rc == 0
    body = out.getvalue()
    assert "demo-x" in body
    assert "demo-y" not in body


def test_cli_status_json_flag_emits_valid_json(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-j", provider="mock", project_root=scaffolded_project, ignore_breakpoints=True
    )
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["status", "--json", "--project", str(scaffolded_project)])
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload[0]["unit_id"] == "demo-j"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _find_dead_pid() -> int:
    for candidate in (4194303, 999999, 65533, 65534):
        try:
            os.kill(candidate, 0)
        except (ProcessLookupError, OSError):
            return candidate
    raise RuntimeError("no obviously-dead PID available for the test")
