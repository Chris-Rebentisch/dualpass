"""State directory conventions, lockfile management, build markers.

Owns the on-disk layout under `.dualpass-state/`. Read-side helpers for `status`
and `retro`; write-side helpers for the controller.

Layout:

    .dualpass-state/
      <unit>-pipeline.lock.json     # single-flight lock for one running unit
      <unit>-events.jsonl           # append-only event stream (observability.py)
      <unit>/                       # per-unit artifact directory
        <stage>-artifact-v<round>.md
        <stage>-review-v<round>.md
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

BuildStatus = Literal["partial", "complete", "blocked"]
ExitSignal = Literal["stop", "continue", "escalate"]
BlockerKind = Literal["architectural", "infrastructure", "spec_defect", "max_rounds_exhausted"]


@dataclass
class BuildMarker:
    """Parsed contents of a `.dualpass-state/<unit>-build-complete.md` marker."""

    unit: str
    stage: str
    status: BuildStatus
    exit_signal: ExitSignal
    blocker_kind: BlockerKind | None
    artifacts_produced: list[str]


def state_dir(project_root: Path) -> Path:
    """Return the `.dualpass-state/` path for a project, creating it if needed."""
    state = project_root / ".dualpass-state"
    state.mkdir(parents=True, exist_ok=True)
    return state


def units_dir(project_root: Path, unit_id: str) -> Path:
    """Return the per-unit artifact directory, creating it if needed."""
    out = state_dir(project_root) / unit_id
    out.mkdir(parents=True, exist_ok=True)
    return out


# ── Lockfile management ──────────────────────────────────────────────────────


def lock_path(unit_id: str, project_root: Path) -> Path:
    """Where the single-flight lock lives for one unit."""
    return state_dir(project_root) / f"{unit_id}-pipeline.lock.json"


def lock_present(unit_id: str, project_root: Path) -> bool:
    """Return True if `.dualpass-state/<unit>-pipeline.lock.json` exists.

    Used by watchers and the controller to enforce single-flight.
    """
    return lock_path(unit_id, project_root).is_file()


def acquire_lock(unit_id: str, project_root: Path) -> bool:
    """Atomically create the lockfile. Returns True on success, False if held.

    Uses `os.O_CREAT | os.O_EXCL` so the file-creation step is atomic — two
    concurrent acquirers cannot both succeed.
    """
    path = lock_path(unit_id, project_root)
    payload = json.dumps(
        {
            "unit": unit_id,
            "pid": os.getpid(),
            "acquired_at": datetime.now(UTC).isoformat(),
        },
        sort_keys=True,
    )
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return True


def release_lock(unit_id: str, project_root: Path) -> bool:
    """Delete the lockfile. Returns True if a file was removed, False if none existed."""
    path = lock_path(unit_id, project_root)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def read_lock(unit_id: str, project_root: Path) -> dict[str, object] | None:
    """Return the lockfile payload, or None if absent."""
    path = lock_path(unit_id, project_root)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Build markers (stub for v0.3.0) ──────────────────────────────────────────


def read_build_marker(unit_id: str, stage: str, project_root: Path) -> BuildMarker | None:
    """Read and parse the build-complete marker for a unit's stage.

    v1.0.0: not wired. The controller does not emit build-complete markers;
    state lives in the event log (`.dualpass-state/<unit>-events.jsonl`).
    This signature is preserved for forward compatibility — projects that
    want operator-readable build markers can implement them on top of the
    event log without changing dualpass's contract.
    """
    raise NotImplementedError(
        "memory.read_build_marker is not wired in v1. The event log is the "
        "canonical state source; see `observability.read_events`."
    )


def list_stuck_markers(project_root: Path) -> list[Path]:
    """Return all `.dualpass-state/*-stuck-*.md` markers."""
    return sorted(state_dir(project_root).glob("*-stuck-*.md"))
