"""State directory conventions, lockfile management, build markers.

Owns the on-disk layout under `.dualpass-state/`. Read-side helpers for `status`
and `retro`; write-side helpers for the controller.

Layout:

    .dualpass-state/
      <unit>-pipeline.lock.json     # single-flight lock for one running unit
      <unit>-events.jsonl           # append-only event stream (observability.py)
      <unit>-build-complete.md      # build-complete marker (YAML frontmatter)
      <unit>/                       # per-unit artifact directory
        <stage>-artifact-v<round>.md
        <stage>-review-v<round>.md
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

BuildStatus = Literal["partial", "complete", "blocked"]
ExitSignal = Literal["stop", "continue", "escalate"]
BlockerKind = Literal["architectural", "infrastructure", "spec_defect", "max_rounds_exhausted"]

_VALID_STATUS: tuple[str, ...] = get_args(BuildStatus)
_VALID_EXIT_SIGNAL: tuple[str, ...] = get_args(ExitSignal)
_VALID_BLOCKER_KIND: tuple[str, ...] = get_args(BlockerKind)

# Fields lifted out of `metadata` into typed BuildMarker attributes. Anything
# else in the frontmatter is preserved verbatim under `metadata`.
_RESERVED_FIELDS: frozenset[str] = frozenset(
    {"unit", "stage", "status", "exit_signal", "blocker_kind", "artifacts_produced"}
)


class BuildMarkerError(Exception):
    """Raised when a build-complete marker exists but is malformed."""


@dataclass
class BuildMarker:
    """Parsed contents of a `.dualpass-state/<unit>-build-complete.md` marker."""

    unit: str
    stage: str
    status: BuildStatus
    exit_signal: ExitSignal
    blocker_kind: BlockerKind | None = None
    artifacts_produced: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


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


# ── Build markers ────────────────────────────────────────────────────────────


def build_marker_path(unit_id: str, project_root: Path) -> Path:
    """Return the on-disk path for a unit's build-complete marker."""
    return state_dir(project_root) / f"{unit_id}-build-complete.md"


def _split_frontmatter(text: str) -> str:
    """Extract the YAML frontmatter body from a marker file.

    The expected shape is::

        ---
        <yaml>
        ---
        <optional body, ignored>

    Returns the inner YAML payload as a string. Raises BuildMarkerError if
    the opening or closing fence is missing.
    """
    # Tolerate a leading BOM or blank lines before the opening fence.
    stripped = text.lstrip("﻿").lstrip("\n")
    if not stripped.startswith("---"):
        raise BuildMarkerError(
            "build marker is missing opening '---' frontmatter fence"
        )
    # Strip the opening fence (and the newline that follows it, if any).
    after_open = stripped[len("---") :]
    if after_open.startswith("\n"):
        after_open = after_open[1:]
    elif after_open.startswith("\r\n"):
        after_open = after_open[2:]

    # Find the closing fence — a line that consists solely of `---`.
    lines = after_open.splitlines()
    closing_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "---":
            closing_idx = idx
            break
    if closing_idx is None:
        raise BuildMarkerError(
            "build marker is missing closing '---' frontmatter fence"
        )
    return "\n".join(lines[:closing_idx])


def read_build_marker(unit_id: str, project_root: Path) -> BuildMarker | None:
    """Parse YAML frontmatter from `.dualpass-state/<unit_id>-build-complete.md`.

    Returns None if the file does not exist. Returns a `BuildMarker` if the
    file exists and frontmatter is valid. Raises `BuildMarkerError` if the
    file exists but the frontmatter is missing, malformed, or carries an
    invalid enum value for `status`, `exit_signal`, or `blocker_kind`.

    The controller calls this on stage exit to act on `exit_signal`
    (CONCEPTS.md §5 — author-driven halt contract).
    """
    path = build_marker_path(unit_id, project_root)
    if not path.is_file():
        return None

    raw = path.read_text(encoding="utf-8")
    yaml_body = _split_frontmatter(raw)

    try:
        data = yaml.safe_load(yaml_body)
    except yaml.YAMLError as exc:
        raise BuildMarkerError(f"build marker frontmatter is not valid YAML: {exc}") from exc

    if data is None:
        raise BuildMarkerError("build marker frontmatter is empty")
    if not isinstance(data, dict):
        raise BuildMarkerError(
            f"build marker frontmatter must be a mapping, got {type(data).__name__}"
        )

    # Required fields ────────────────────────────────────────────────────────
    for required in ("unit", "stage", "status", "exit_signal"):
        if required not in data:
            raise BuildMarkerError(f"build marker missing required field '{required}'")

    unit = data["unit"]
    if not isinstance(unit, str):
        raise BuildMarkerError("build marker field 'unit' must be a string")
    if unit != unit_id:
        raise BuildMarkerError(
            f"build marker field 'unit' ({unit!r}) does not match expected unit_id ({unit_id!r})"
        )

    stage = data["stage"]
    if not isinstance(stage, str):
        raise BuildMarkerError("build marker field 'stage' must be a string")

    status = data["status"]
    if status not in _VALID_STATUS:
        raise BuildMarkerError(
            f"build marker field 'status' has invalid value {status!r}; "
            f"expected one of {list(_VALID_STATUS)}"
        )

    exit_signal = data["exit_signal"]
    if exit_signal not in _VALID_EXIT_SIGNAL:
        raise BuildMarkerError(
            f"build marker field 'exit_signal' has invalid value {exit_signal!r}; "
            f"expected one of {list(_VALID_EXIT_SIGNAL)}"
        )

    # Optional fields ────────────────────────────────────────────────────────
    blocker_kind = data.get("blocker_kind")
    if blocker_kind is not None and blocker_kind not in _VALID_BLOCKER_KIND:
        raise BuildMarkerError(
            f"build marker field 'blocker_kind' has invalid value {blocker_kind!r}; "
            f"expected null or one of {list(_VALID_BLOCKER_KIND)}"
        )

    artifacts_raw = data.get("artifacts_produced", [])
    if artifacts_raw is None:
        artifacts: list[str] = []
    elif isinstance(artifacts_raw, list):
        if not all(isinstance(item, str) for item in artifacts_raw):
            raise BuildMarkerError(
                "build marker field 'artifacts_produced' must be a list of strings"
            )
        artifacts = list(artifacts_raw)
    else:
        raise BuildMarkerError(
            "build marker field 'artifacts_produced' must be a list of strings"
        )

    # Anything we did not lift out is preserved as `metadata`.
    metadata: dict[str, Any] = {k: v for k, v in data.items() if k not in _RESERVED_FIELDS}

    return BuildMarker(
        unit=unit,
        stage=stage,
        status=status,
        exit_signal=exit_signal,
        blocker_kind=blocker_kind,
        artifacts_produced=artifacts,
        metadata=metadata,
    )


def write_build_marker(marker: BuildMarker, project_root: Path) -> Path:
    """Write a `BuildMarker` to `.dualpass-state/<unit>-build-complete.md`.

    The inverse of `read_build_marker`. Useful for tests and mock providers
    that need to simulate an author-emitted build-complete marker.

    Validates enum fields on the way out so a round-trip through disk cannot
    introduce a value `read_build_marker` would reject.
    """
    if marker.status not in _VALID_STATUS:
        raise BuildMarkerError(
            f"refusing to write build marker with invalid status {marker.status!r}"
        )
    if marker.exit_signal not in _VALID_EXIT_SIGNAL:
        raise BuildMarkerError(
            f"refusing to write build marker with invalid exit_signal {marker.exit_signal!r}"
        )
    if marker.blocker_kind is not None and marker.blocker_kind not in _VALID_BLOCKER_KIND:
        raise BuildMarkerError(
            f"refusing to write build marker with invalid blocker_kind {marker.blocker_kind!r}"
        )

    payload: dict[str, Any] = {
        "unit": marker.unit,
        "stage": marker.stage,
        "status": marker.status,
        "exit_signal": marker.exit_signal,
        "blocker_kind": marker.blocker_kind,
        "artifacts_produced": list(marker.artifacts_produced),
    }
    # Extra metadata is appended after the reserved fields so the file reads
    # in a predictable order.
    for key, value in marker.metadata.items():
        if key in _RESERVED_FIELDS:
            # Defensive: never let metadata shadow a reserved field on write.
            continue
        payload[key] = value

    body = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)
    text = f"---\n{body}---\n"

    path = build_marker_path(marker.unit, project_root)
    path.write_text(text, encoding="utf-8")
    return path


def list_stuck_markers(project_root: Path) -> list[Path]:
    """Return all `.dualpass-state/*-stuck-*.md` markers."""
    return sorted(state_dir(project_root).glob("*-stuck-*.md"))
