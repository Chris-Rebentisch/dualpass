"""Stage-context bundle + precedent-cache builders.

These two artifacts compress canonical project docs + recent peer artifacts
into the form an agent stage skill bootstraps from. Per CONCEPTS.md, explicit
context curation is a load-bearing engineering responsibility — the harness
pre-builds the bundles so each stage starts from a small, well-shaped prompt
seed rather than re-walking the whole project tree.

Both builders are deterministic given inputs (no wall-clock reads, no random
ordering). Output is written atomically: content goes to a sibling `.tmp` file
first and is then renamed via `os.replace` so a partial write can never be
observed by a reader.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Source-doc table for the stage-context bundle ────────────────────────────
# Each tuple is (relative-path-from-project-root, line-cap). The cap is enforced
# verbatim — we don't try to be clever about paragraph boundaries because the
# downstream prompt is happy with a hard cut.
_PROJECT_DOC_SOURCES: tuple[tuple[str, int], ...] = (
    ("docs/_project/PROJECT.md", 60),
    ("docs/_project/DECISIONS.md", 80),
    ("docs/_project/BACKLOG.md", 40),
    ("docs/_project/DOC-MAP.md", 40),
)

# How many lines of a predecessor artifact to carry forward. Bigger than the
# project-doc caps because the predecessor is the load-bearing input for the
# next stage.
_PREDECESSOR_LINE_CAP = 200

# Line cap per peer artifact in the precedent cache. Three peers at 120 lines
# each keeps the cache around a few hundred lines — comfortably inside a
# prompt's context budget without flattening every nuance.
_PRECEDENT_LINE_CAP = 120


def _read_capped(path: Path, line_cap: int) -> str | None:
    """Return up to `line_cap` lines of `path`, or None if the file is missing.

    We split on universal newlines and rejoin with `\n` so the bundle has a
    predictable shape regardless of the source file's line endings.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    lines = text.splitlines()
    truncated = lines[:line_cap]
    return "\n".join(truncated)


def _atomic_write(target: Path, content: str) -> None:
    """Write `content` to `target` via a `.tmp` sibling + `os.replace`.

    Creates parent directories if needed. The temp filename is colocated with
    the target so the final `os.replace` stays within a single filesystem
    (avoiding cross-device rename failures).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


def _format_section(title: str, body: str | None, *, missing_note: str) -> str:
    """Render one Markdown section. Body=None becomes the `missing_note` line."""
    header = f"## {title}"
    if body is None:
        return f"{header}\n\n{missing_note}\n"
    return f"{header}\n\n{body}\n"


def _find_newest_predecessor(
    unit_dir: Path,
    predecessor_stage: str,
) -> Path | None:
    """Pick the newest FINAL for a predecessor stage, falling back to non-FINAL drafts.

    Filenames look like `<stage>-v<N>.md` and `<stage>-v<N>-FINAL.md`. We prefer
    a FINAL when one exists for the stage; otherwise we fall back to the newest
    draft. "Newest" is by modification time so the caller doesn't have to parse
    version numbers — version order and mtime order agree in practice.
    """
    if not unit_dir.is_dir():
        return None

    finals = sorted(
        unit_dir.glob(f"{predecessor_stage}-v*-FINAL.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if finals:
        return finals[0]

    # Fallback: any draft of that stage. We exclude `-FINAL` files (already
    # checked above) so the glob can't double-count.
    drafts = [
        p
        for p in unit_dir.glob(f"{predecessor_stage}-v*.md")
        if not p.name.endswith("-FINAL.md")
    ]
    if not drafts:
        return None
    drafts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return drafts[0]


def build_stage_context(
    *,
    unit_id: str,
    stage: str,
    project_root: Path,
    predecessor_stage: str | None = None,
) -> Path:
    """Write a compressed stage-context bundle for an agent prompt.

    See the module docstring for the file format. Returns the absolute path
    of the written bundle.
    """
    project_root = project_root.resolve()
    output_path = project_root / ".dualpass-state" / f"{unit_id}-stage-context.md"

    sections: list[str] = []

    # Header — the only place unit_id / stage / predecessor_stage appear, so
    # downstream tooling can read them back from the bundle alone if needed.
    header_lines = [
        "# Stage Context Bundle",
        "",
        f"- unit_id: {unit_id}",
        f"- stage: {stage}",
        f"- predecessor_stage: {predecessor_stage if predecessor_stage else '(none)'}",
        "",
    ]
    sections.append("\n".join(header_lines))

    # Canonical project docs. A missing file produces an explicit "(not found)"
    # marker rather than a silent gap, so the reader knows the slot was checked.
    for rel_path, line_cap in _PROJECT_DOC_SOURCES:
        source = project_root / rel_path
        body = _read_capped(source, line_cap)
        doc_name = Path(rel_path).name
        if body is None:
            title = f"{doc_name} (first {line_cap} lines, truncated)"
            sections.append(_format_section(title, None, missing_note="(not found)"))
        else:
            title = f"{doc_name} (first {line_cap} lines, truncated)"
            sections.append(_format_section(title, body, missing_note="(not found)"))

    # Predecessor artifact. The "(no predecessor stage)" case covers both an
    # explicit None and a stage that just happens to have no prior artifact —
    # the caller gets the same affordance either way.
    pred_title_stage = predecessor_stage or "predecessor"
    if predecessor_stage is None:
        sections.append(
            _format_section(
                f"Predecessor stage: {pred_title_stage}",
                None,
                missing_note="(no predecessor stage)",
            )
        )
    else:
        unit_dir = project_root / ".dualpass-state" / unit_id
        pred_path = _find_newest_predecessor(unit_dir, predecessor_stage)
        if pred_path is None:
            sections.append(
                _format_section(
                    f"Predecessor stage: {predecessor_stage}",
                    None,
                    missing_note="(no predecessor stage)",
                )
            )
        else:
            body = _read_capped(pred_path, _PREDECESSOR_LINE_CAP)
            title = (
                f"Predecessor stage: {predecessor_stage} "
                f"({pred_path.name}, first {_PREDECESSOR_LINE_CAP} lines, truncated)"
            )
            sections.append(_format_section(title, body, missing_note="(not found)"))

    bundle = "\n".join(sections).rstrip() + "\n"
    _atomic_write(output_path, bundle)
    return output_path


def _collect_peer_finals(
    state_root: Path,
    stage: str,
    current_unit_id: str,
) -> list[Path]:
    """Collect FINAL artifacts for `stage` across every prior unit's subdir.

    Skips the current unit's own subdir so the cache reflects precedent from
    other units, not from the unit being built. Subdirectories without a
    matching FINAL contribute nothing.
    """
    if not state_root.is_dir():
        return []

    finals: list[Path] = []
    for child in state_root.iterdir():
        if not child.is_dir():
            continue
        if child.name == current_unit_id:
            continue
        for final in child.glob(f"{stage}-v*-FINAL.md"):
            finals.append(final)
    return finals


def build_precedent_cache(
    *,
    unit_id: str,
    stage: str,
    project_root: Path,
    peer_count: int = 3,
) -> Path:
    """Write a compressed precedent cache for a stage.

    See the module docstring for the file format. Returns the absolute path
    of the written cache.
    """
    project_root = project_root.resolve()
    state_root = project_root / ".dualpass-state"
    output_path = state_root / f"{unit_id}-precedent-cache.md"

    sections: list[str] = []

    header_lines = [
        "# Precedent Cache",
        "",
        f"- unit_id: {unit_id}",
        f"- stage: {stage}",
        f"- peer_count_requested: {peer_count}",
    ]
    sections.append("\n".join(header_lines) + "\n")

    finals = _collect_peer_finals(state_root, stage, unit_id)
    finals.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    selected = finals[:peer_count]

    if not selected:
        sections.append(
            _format_section(
                "Peer precedent",
                None,
                missing_note="(no precedent units found)",
            )
        )
    else:
        for peer in selected:
            # `peer.parent.name` is the peer's unit_id — surface it so the
            # reader can attribute each excerpt back to its source unit.
            peer_unit = peer.parent.name
            body = _read_capped(peer, _PRECEDENT_LINE_CAP)
            title = (
                f"Peer unit: {peer_unit} ({peer.name}, "
                f"first {_PRECEDENT_LINE_CAP} lines, truncated)"
            )
            sections.append(
                _format_section(title, body, missing_note="(not found)")
            )

    cache = "\n".join(sections).rstrip() + "\n"
    _atomic_write(output_path, cache)
    return output_path
