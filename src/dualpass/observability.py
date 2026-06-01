"""Append-only structured event stream + status renderer.

Every controller turn writes one JSON object per line to
`.dualpass-state/<unit>-events.jsonl`. The format is JSON-newline so it's
greppable, tail-friendly, and trivial to load into a notebook later.

We deliberately keep the event vocabulary closed (`EventType`) so consumers
can pattern-match without worrying about typos in producer code.

`render_status` is the read-side: walk `.dualpass-state/`, find every unit's
event log + lockfile, and produce either a rich-formatted human table or a
JSON document. The CLI's `dualpass status` is a thin wrapper around it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

EventType = Literal[
    "unit_started",
    "unit_completed",
    "unit_aborted",
    "stage_round_started",
    "stage_completed",
    "stage_revision_requested",
    "stage_blocked",
    "breakpoint_hit",
    "circuit_breaker_tripped",
    "lockfile_acquired",
    "lockfile_released",
    "lockfile_conflict",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Event:
    """One row in the event stream."""

    type: EventType
    unit: str
    stage: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    iso_timestamp: str = field(default_factory=_now_iso)


def event_log_path(unit_id: str, project_root: Path) -> Path:
    """Where to append events for this unit."""
    from dualpass.memory import state_dir

    return state_dir(project_root) / f"{unit_id}-events.jsonl"


def emit(event: Event, *, project_root: Path) -> None:
    """Append the event to `.dualpass-state/<unit>-events.jsonl`."""
    path = event_log_path(event.unit, project_root)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(event), sort_keys=True) + "\n")


def read_events(unit_id: str, project_root: Path) -> list[Event]:
    """Read every event for a unit. Returns [] if the log doesn't exist yet."""
    path = event_log_path(unit_id, project_root)
    if not path.is_file():
        return []
    out: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out.append(Event(**d))
    return out


# ── Status rendering ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UnitStatus:
    """Aggregate status of one unit, derived from its event log + lockfile state."""

    unit_id: str
    state: Literal[
        "in-flight",
        "completed",
        "paused-at-breakpoint",
        "blocked",
        "circuit-tripped",
        "stale-lock",
        "unknown",
    ]
    current_stage: str | None
    stages_completed: list[str]
    last_event_type: str | None
    last_event_iso: str | None
    lock_held: bool
    lock_pid: int | None
    paused_at: str | None  # stage name if paused at breakpoint
    blocked_at: str | None  # stage name if blocked


def _list_unit_ids(project_root: Path) -> list[str]:
    """Find every unit that has at least one event log under .dualpass-state/."""
    from dualpass.memory import state_dir

    sdir = state_dir(project_root)
    suffix = "-events.jsonl"
    ids = sorted(
        p.name[: -len(suffix)] for p in sdir.iterdir() if p.is_file() and p.name.endswith(suffix)
    )
    return ids


def _aggregate_unit(unit_id: str, project_root: Path) -> UnitStatus:
    """Roll a unit's event log + lockfile into one UnitStatus row."""
    from dualpass.memory import lock_path, read_lock

    events = read_events(unit_id, project_root)
    lock_record = read_lock(unit_id, project_root)
    lock_held = lock_path(unit_id, project_root).is_file()
    lock_pid_raw = lock_record.get("pid") if lock_record else None
    lock_pid = int(lock_pid_raw) if isinstance(lock_pid_raw, int | str) else None

    if not events:
        return UnitStatus(
            unit_id=unit_id,
            state="unknown",
            current_stage=None,
            stages_completed=[],
            last_event_type=None,
            last_event_iso=None,
            lock_held=lock_held,
            lock_pid=lock_pid,
            paused_at=None,
            blocked_at=None,
        )

    last = events[-1]
    stages_completed = [
        e.stage for e in events if e.type == "stage_completed" and isinstance(e.stage, str)
    ]
    current_stage: str | None = None
    for e in reversed(events):
        if e.stage:
            current_stage = e.stage
            break

    # The terminal event for state classification is the most recent NON-lockfile
    # event. Lockfile events bracket every run but they don't carry semantic
    # state — `lockfile_released` after a breakpoint pause should still report
    # 'paused-at-breakpoint', not 'blocked'.
    _LOCK_EVENTS = {"lockfile_acquired", "lockfile_released", "lockfile_conflict"}
    semantic_last = None
    for e in reversed(events):
        if e.type not in _LOCK_EVENTS:
            semantic_last = e
            break

    paused_at = None
    blocked_at = None
    state: str
    if any(e.type == "unit_completed" for e in events):
        state = "completed"
    elif semantic_last is None:
        state = "in-flight" if lock_held else "unknown"
    elif semantic_last.type == "circuit_breaker_tripped":
        state = "circuit-tripped"
        blocked_at = semantic_last.stage
    elif semantic_last.type == "stage_blocked":
        state = "blocked"
        blocked_at = semantic_last.stage
    elif semantic_last.type == "breakpoint_hit":
        state = "paused-at-breakpoint"
        paused_at = semantic_last.stage
    elif lock_held:
        # PID is dead but lock wasn't released → stale.
        if lock_pid is not None and not _pid_is_alive(lock_pid):
            state = "stale-lock"
        else:
            state = "in-flight"
    elif last.type == "lockfile_released":
        # Lockfile released but no unit_completed → run was aborted mid-flight.
        state = "blocked"
        blocked_at = current_stage
    else:
        state = "in-flight"

    return UnitStatus(
        unit_id=unit_id,
        state=state,
        current_stage=current_stage,
        stages_completed=stages_completed,
        last_event_type=last.type,
        last_event_iso=last.iso_timestamp,
        lock_held=lock_held,
        lock_pid=lock_pid,
        paused_at=paused_at,
        blocked_at=blocked_at,
    )


def _pid_is_alive(pid: int) -> bool:
    """Same liveness probe the watcher uses — `os.kill(pid, 0)`."""
    import os

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def status_for(unit_id: str, project_root: Path) -> UnitStatus:
    """Public helper: aggregate a single unit's status."""
    return _aggregate_unit(unit_id, project_root)


def status_all(project_root: Path) -> list[UnitStatus]:
    """Aggregate status for every unit that has an event log."""
    return [_aggregate_unit(uid, project_root) for uid in _list_unit_ids(project_root)]


def render_status(project_root: Path, *, unit_id: str | None = None, as_json: bool = False) -> int:
    """Print status to stdout. Returns process exit code (always 0 — informational)."""
    rows = [status_for(unit_id, project_root)] if unit_id else status_all(project_root)

    if as_json:
        payload = [asdict(r) for r in rows]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if not rows:
        print("(no units found in .dualpass-state/)")
        return 0

    # Rich table for the human form. Lazy import — rich is a runtime dep but we
    # don't want to pay the import cost on every CLI invocation.
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="dualpass — unit status", show_lines=False)
    table.add_column("Unit", style="cyan", no_wrap=True)
    table.add_column("State", style="bold")
    table.add_column("Stage")
    table.add_column("Completed", overflow="fold")
    table.add_column("Last event")
    table.add_column("Lock")

    for r in rows:
        state_color = {
            "completed": "green",
            "in-flight": "yellow",
            "paused-at-breakpoint": "blue",
            "blocked": "red",
            "circuit-tripped": "red",
            "stale-lock": "red",
            "unknown": "white",
        }.get(r.state, "white")
        lock_repr = f"pid={r.lock_pid}" if r.lock_held else "-"
        stage_repr = r.current_stage or "-"
        completed_repr = ", ".join(r.stages_completed) or "-"
        last_repr = (
            f"{r.last_event_type} @ {r.last_event_iso[:19]}"
            if r.last_event_type and r.last_event_iso
            else "-"
        )
        table.add_row(
            r.unit_id,
            f"[{state_color}]{r.state}[/{state_color}]",
            stage_repr,
            completed_repr,
            last_repr,
            lock_repr,
        )
    console.print(table)
    return 0
