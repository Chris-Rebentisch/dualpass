"""Logging, structured event emission, cost ledger.

dualpass writes two log streams per unit:

1. Per-turn human-readable logs at .dualpass-state/logs/<unit>-<stage>-r<round>-<ts>.log
2. Structured events at .dualpass-state/<unit>-events.jsonl (one JSON object per line)

v0.1.0a0 status: stub. The rich-based renderer for `dualpass status` lands here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


EventType = Literal[
    "stage_started",
    "stage_completed",
    "stage_blocked",
    "review_requested",
    "review_returned",
    "auto_relaunch",
    "circuit_breaker_tripped",
    "fallback_activated",
    "lockfile_acquired",
    "lockfile_released",
]


@dataclass
class Event:
    type: EventType
    unit: str
    stage: str | None
    iso_timestamp: str
    payload: dict[str, Any]


def emit(event: Event, *, project_root: Path) -> None:
    """Append an event to .dualpass-state/<unit>-events.jsonl."""
    raise NotImplementedError("observability.emit — landing in v0.2.0")


def render_status(project_root: Path, *, unit_id: str | None = None, as_json: bool = False) -> int:
    """Print human-readable (rich) or machine-readable (JSON) status. Returns process exit code."""
    raise NotImplementedError("observability.render_status — landing in v0.2.0")
