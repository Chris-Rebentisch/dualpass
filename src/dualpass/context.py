"""Stage-context bundle + precedent-cache builders (deferred to v1.1).

These two artifacts compress canonical project docs + recent peer artifacts
into the form a stage skill bootstraps from. Per CONCEPTS.md §4, explicit
context curation is the load-bearing engineering responsibility — not a
framework concern.

**v1.0.0 status:** the bootstrap shapes live in this module's signatures so
downstream code can import them, but the builders themselves are not wired.
v1 stage skills curate their own context (from `docs/_project/` and prior
artifacts) per the SKILL.md instructions; the harness does NOT pre-build a
context bundle. The functions below raise NotImplementedError; v1.1 may
choose to implement them once usage patterns clarify whether the abstraction
is worth the indirection.
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
    """Compress canonical project docs + predecessor artifact into a stage-context bundle.

    Writes to `.dualpass-state/<unit>-stage-context.md` and returns the path.

    v1.0.0: not wired — stage skills curate context inline per SKILL.md.
    """
    raise NotImplementedError(
        "context.build_stage_context is not wired in v1. Stage skills curate "
        "their own context per SKILL.md; a pre-built bundle is a v1.1+ concept."
    )


def build_precedent_cache(
    unit_id: str,
    stage: str,
    *,
    project_root: Path,
    output_path: Path,
    count: int = 3,
) -> Path:
    """Compress the N most-recent ratified peer-stage artifacts into a precedent cache.

    Writes to `.dualpass-state/<unit>-precedent-cache.md` and returns the path.

    v1.0.0: not wired — see `build_stage_context`.
    """
    raise NotImplementedError(
        "context.build_precedent_cache is not wired in v1. Stage skills read "
        "recent peer artifacts inline per SKILL.md; a pre-built cache is a v1.1+ concept."
    )
