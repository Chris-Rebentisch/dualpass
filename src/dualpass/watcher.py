"""Background watchers — research-complete → outline, prompt-drafts, handoff-finals.

Generalized from GrACE pipeline patterns. Ships with the §6.10 Fix 1 (whitespace-robust
PID parsing per BashFAQ/001) and Fix 2 (pipeline-lock guard + splits_into frontmatter
check) baked in.

v0.1.0a0 status: stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


WatcherName = Literal["research", "prompt", "handoff"]
WatcherStatus = Literal["running", "stopped", "stale-pid"]


@dataclass
class WatcherState:
    name: WatcherName
    status: WatcherStatus
    pid: int | None
    last_scan_iso: str | None


def start(name: WatcherName, *, provider: Literal["live", "mock"] = "live") -> int:
    """Launch a watcher daemon. Returns the watcher's PID."""
    raise NotImplementedError("watcher.start — landing in v0.3.0")


def stop(name: WatcherName) -> bool:
    """Stop a running watcher. Returns True if a watcher was stopped, False if none was running."""
    raise NotImplementedError("watcher.stop — landing in v0.3.0")


def status(name: WatcherName | None = None) -> list[WatcherState]:
    """Report status of one or all watchers."""
    raise NotImplementedError("watcher.status — landing in v0.3.0")


def seed_state_before_live(project_root: Path) -> dict[str, list[str]]:
    """Mark existing artifacts as seen before bringing watchers live.

    Per the §16.1 GrACE retro lesson: without seeding, starting `--provider live` on a
    stale state file stampedes the entire research/handoff backlog. Returns a report
    of what was seeded per watcher.
    """
    raise NotImplementedError("watcher.seed_state_before_live — landing in v0.3.0")
