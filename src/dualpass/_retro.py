"""`dualpass retro` implementation.

Two modes:

  - **single-unit:** `dualpass retro --unit my-001`
      Opens `docs/_project/RETROSPECTIVES/my-001.md`. If it doesn't exist,
      writes a template stub seeded with the unit's run summary (stages,
      verdicts, breakpoints hit) and reports the path. The user fills in the
      narrative bits.

  - **range:** `dualpass retro --range '001..010' --output rollup.md`
      Concatenates the matching per-unit retros into one rollup with a
      table-of-contents frontmatter and each unit's body inlined. Useful for
      end-of-cycle reviews.

The retro format is deliberately just markdown — no special parser, no
schema. The harness's job is to make creation cheap and aggregation
mechanical; the value is in what the operator writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dualpass import observability

_RETRO_DEFAULT_DIR = "docs/_project/RETROSPECTIVES"


# ── Single-unit retro ────────────────────────────────────────────────────────


def retro_path(project_root: Path, unit_id: str) -> Path:
    """Return the canonical retro path for one unit."""
    return project_root / _RETRO_DEFAULT_DIR / f"{unit_id}.md"


def _build_unit_template(unit_id: str, project_root: Path) -> str:
    """Seed a new retro file with metadata from the unit's event log."""
    summary = observability.status_for(unit_id, project_root)
    stages_line = ", ".join(summary.stages_completed) if summary.stages_completed else "(none)"
    paused_repr = summary.paused_at or "-"
    blocked_repr = summary.blocked_at or "-"
    return (
        f"---\n"
        f"unit: {unit_id}\n"
        f"state: {summary.state}\n"
        f"created: {datetime.now(UTC).date().isoformat()}\n"
        f"---\n\n"
        f"# Retrospective — {unit_id}\n\n"
        f"## At-a-glance\n\n"
        f"- **final state:** `{summary.state}`\n"
        f"- **stages completed:** {stages_line}\n"
        f"- **paused at breakpoint:** `{paused_repr}`\n"
        f"- **blocked at:** `{blocked_repr}`\n\n"
        f"## What went well\n\n"
        f"<!-- one or two lines per item -->\n\n"
        f"## What went wrong\n\n"
        f"<!-- include failure modes you saw — author confusion, reviewer drift, etc. -->\n\n"
        f"## Surprises\n\n"
        f"<!-- behaviors the harness or the agents did that you didn't expect -->\n\n"
        f"## Changes for next time\n\n"
        f"<!-- skill edits, config tweaks, gate adjustments, etc. -->\n"
    )


def open_or_create(project_root: Path, unit_id: str) -> tuple[Path, bool]:
    """Return (path, created_now). Writes a template if the retro doesn't exist."""
    path = retro_path(project_root, unit_id)
    if path.is_file():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_build_unit_template(unit_id, project_root), encoding="utf-8")
    return path, True


# ── Range aggregation ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class RangeResult:
    output: Path
    included: list[str]
    missing: list[str]


_RANGE_PATTERN = re.compile(r"^(.+?)\.\.(.+)$")


def parse_range(spec: str) -> list[str]:
    """Parse '001..010' or 'prefix-001..prefix-005' into a list of unit IDs.

    Handles zero-padded numeric ranges with or without a common prefix.
    Raises ValueError on malformed input.
    """
    m = _RANGE_PATTERN.match(spec)
    if not m:
        raise ValueError(
            f"invalid range {spec!r} — expected 'start..end' (e.g. '001..010' or 'my-001..my-010')"
        )
    start, end = m.group(1), m.group(2)

    # Split prefix from numeric tail on both endpoints. If the prefix differs,
    # we refuse — that's almost always a typo.
    s_prefix, s_num = _split_num(start)
    e_prefix, e_num = _split_num(end)
    if s_num is None or e_num is None:
        raise ValueError("range endpoints must end in a number (e.g. 001..010)")
    if s_prefix != e_prefix:
        raise ValueError(f"range endpoints have different prefixes: {s_prefix!r} vs {e_prefix!r}")
    if e_num < s_num:
        raise ValueError(f"range end {e_num} is before start {s_num}")
    width = max(len(_num_tail(start)), len(_num_tail(end)))
    return [f"{s_prefix}{str(n).zfill(width)}" for n in range(s_num, e_num + 1)]


def _split_num(s: str) -> tuple[str, int | None]:
    """Split a string into ('prefix', trailing_integer). Returns (s, None) if no tail."""
    tail = _num_tail(s)
    if not tail:
        return s, None
    return s[: -len(tail)], int(tail)


def _num_tail(s: str) -> str:
    """Return the trailing-digit substring of s, or ''."""
    i = len(s)
    while i > 0 and s[i - 1].isdigit():
        i -= 1
    return s[i:]


def aggregate(project_root: Path, unit_ids: list[str], output: Path | None = None) -> RangeResult:
    """Concatenate per-unit retros into one rollup. Returns (output, included, missing)."""
    if not unit_ids:
        raise ValueError("aggregate() called with empty unit_ids")

    output_path = (
        output
        if output is not None
        else project_root / _RETRO_DEFAULT_DIR / f"rollup-{unit_ids[0]}-to-{unit_ids[-1]}.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    missing: list[str] = []
    chunks: list[str] = []

    for uid in unit_ids:
        path = retro_path(project_root, uid)
        if not path.is_file():
            missing.append(uid)
            continue
        included.append(uid)
        chunks.append(f"\n\n## {uid}\n\n{path.read_text(encoding='utf-8').rstrip()}\n")

    header = (
        f"---\n"
        f"range: {unit_ids[0]}..{unit_ids[-1]}\n"
        f"included_count: {len(included)}\n"
        f"missing_count: {len(missing)}\n"
        f"generated: {datetime.now(UTC).date().isoformat()}\n"
        f"---\n\n"
        f"# Rollup retrospective — {unit_ids[0]} → {unit_ids[-1]}\n\n"
        f"## Table of contents\n\n"
    )
    toc = "\n".join(f"- [{uid}](#{uid})" for uid in included) or "(no retros found)"
    if missing:
        toc += "\n\n**Missing retros:** " + ", ".join(f"`{u}`" for u in missing)

    output_path.write_text(header + toc + "\n" + "".join(chunks), encoding="utf-8")
    return RangeResult(output=output_path, included=included, missing=missing)
