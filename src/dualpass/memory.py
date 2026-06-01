"""State directory conventions, marker readers, lockfile management.

Owns the layout under .dualpass-state/ and units/<unit-id>/. Read-only helpers
for the CLI's `status` and `retro` commands; write-side helpers used by the
controller and stage runners.

v0.1.0a0 status: stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BuildStatus = Literal["partial", "complete", "blocked"]
ExitSignal = Literal["stop", "continue", "escalate"]
BlockerKind = Literal["architectural", "infrastructure", "spec_defect"]


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
    """Return the .dualpass-state/ path for a project, creating it if needed."""
    state = project_root / ".dualpass-state"
    state.mkdir(parents=True, exist_ok=True)
    return state


def lock_present(unit_id: str, project_root: Path) -> bool:
    """Return True if .dualpass-state/<unit>-pipeline.lock.json exists.

    Used by watchers (per §6.10 Fix 2) and the controller to enforce single-flight.
    """
    return (state_dir(project_root) / f"{unit_id}-pipeline.lock.json").is_file()


def read_build_marker(unit_id: str, stage: str, project_root: Path) -> BuildMarker | None:
    """Read and parse the build-complete marker for a unit's stage."""
    raise NotImplementedError("memory.read_build_marker — landing in v0.2.0")


def list_stuck_markers(project_root: Path) -> list[Path]:
    """Return all `.dualpass-state/*-stuck-*.md` markers."""
    return sorted(state_dir(project_root).glob("*-stuck-*.md"))
