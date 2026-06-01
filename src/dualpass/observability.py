"""Append-only structured event stream.

Every controller turn writes one JSON object per line to
`.dualpass-state/<unit>-events.jsonl`. The format is JSON-newline so it's
greppable, tail-friendly, and trivial to load into a notebook later.

We deliberately keep the event vocabulary closed (`EventType`) so consumers
can pattern-match without worrying about typos in producer code.
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


def render_status(project_root: Path, *, unit_id: str | None = None, as_json: bool = False) -> int:
    """Reserved for `dualpass status` — lands when the UX is designed."""
    raise NotImplementedError("observability.render_status — landing in v0.3.0")
