"""Stage-context bundle + precedent-cache builders.

These are the two artifacts that compress canonical project docs + recent peer artifacts
into the form a stage skill bootstraps from. Per CONCEPTS.md §4, explicit context
curation is the load-bearing engineering responsibility — not a framework concern.

v0.1.0a0 status: stub.
"""

from __future__ import annotations

from pathlib import Path


def build_stage_context(
    unit_id: str,
    stage: str,
    *,
    project_root: Path,
    output_path: Path,
) -> Path:
    """Compress canonical project docs + predecessor FINAL into a stage-context bundle.

    Writes to .dualpass-state/<unit>-stage-context.md and returns the path.
    """
    raise NotImplementedError("context.build_stage_context — landing in v0.2.0")


def build_precedent_cache(
    unit_id: str,
    stage: str,
    *,
    project_root: Path,
    output_path: Path,
    count: int = 3,
) -> Path:
    """Compress the N most recent ratified peer-stage FINALs into a precedent cache.

    Writes to .dualpass-state/<unit>-precedent-cache.md and returns the path.
    """
    raise NotImplementedError("context.build_precedent_cache — landing in v0.2.0")
