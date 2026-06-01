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

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dualpass import observability
from dualpass.memory import state_dir

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

    patterns_section = aggregate_patterns(unit_ids=unit_ids, project_root=project_root)

    output_path.write_text(
        header + toc + "\n\n" + patterns_section.rstrip() + "\n" + "".join(chunks),
        encoding="utf-8",
    )
    return RangeResult(output=output_path, included=included, missing=missing)


# ── Cross-unit pattern aggregation ──────────────────────────────────────────


# A tiny English stoplist. Keeps the analysis cheap and dependency-free; the
# goal is to surface candidate phrases, not to do real NLP.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the",
        "and", "or", "but", "if", "then", "so", "because",
        "is", "was", "were", "are", "be", "been", "being",
        "have", "has", "had", "do", "does", "did",
        "i", "you", "we", "they", "it", "he", "she",
        "this", "that", "these", "those",
        "to", "of", "in", "on", "for", "with", "at", "by", "from",
        "as", "not", "no", "yes",
        "my", "our", "their", "its",
        "will", "would", "should", "can", "could", "may", "might",
        "than", "too", "very", "just",
    }
)

# Section headers we scan in unit retros for free-form lessons.
_RETRO_SCAN_HEADERS: tuple[str, ...] = (
    "## What went wrong",
    "## Changes for next time",
    "## Friction patterns",
)


def _extract_retro_sections(retro_text: str) -> str:
    """Return the concatenated body of the headers in `_RETRO_SCAN_HEADERS`.

    Stops at the next `## ` header. Returns "" if none of the headers are
    present.
    """
    lines = retro_text.splitlines()
    out: list[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            capture = any(stripped == h for h in _RETRO_SCAN_HEADERS)
            continue
        if capture:
            out.append(line)
    return "\n".join(out)


_TOKEN_RE = re.compile(r"[a-z][a-z'-]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip non-alpha, drop stopwords and very short tokens."""
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if tok not in _STOPWORDS and len(tok) >= 3
    ]


def _read_events_raw(unit_id: str, project_root: Path) -> list[dict]:
    """Read the unit's event log as raw dicts. Missing log returns []."""
    path = state_dir(project_root) / f"{unit_id}-events.jsonl"
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Bad rows are skipped silently — the aggregator is a reporting
            # tool, not a validator.
            continue
    return out


def aggregate_patterns(
    *,
    unit_ids: list[str],
    project_root: Path,
) -> str:
    """Return a markdown section: 'Patterns across N units'.

    For each unit, opens `.dualpass-state/<unit>-events.jsonl`, counts the
    event-type occurrences grouped by (stage, event_type), and aggregates
    across all units into a single table. Below the table, lists the top 5
    (stage, event_type) combinations that occurred in more than half the
    units — the "recurring friction" patterns worth hardening.

    Also walks each unit's retro markdown (if present) and extracts unigrams
    and bigrams from the bodies of `## What went wrong`, `## Changes for
    next time`, and `## Friction patterns` sections. Tokens appearing across
    3 or more units' retros are surfaced as candidate cross-unit patterns
    (capped at 12).

    Missing event logs and missing retros are skipped silently. Always
    returns at least the section header, even when there is no data.
    """
    n_units = len(unit_ids)
    lines: list[str] = [f"## Patterns across {n_units} units", ""]

    # ── Event aggregation ────────────────────────────────────────────────
    # Counts keyed on (stage, event_type). `units_with` tracks the set of
    # unit_ids that contributed at least one matching event — that's how we
    # compute the "Units with >=1" column and the >half-of-units filter.
    totals: Counter[tuple[str, str]] = Counter()
    units_with: dict[tuple[str, str], set[str]] = {}
    units_with_any_events = 0

    for uid in unit_ids:
        events = _read_events_raw(uid, project_root)
        if not events:
            continue
        units_with_any_events += 1
        seen_in_this_unit: set[tuple[str, str]] = set()
        for ev in events:
            stage = ev.get("stage") or "-"
            event_type = ev.get("type") or "-"
            key = (stage, event_type)
            totals[key] += 1
            seen_in_this_unit.add(key)
        for key in seen_in_this_unit:
            units_with.setdefault(key, set()).add(uid)

    if totals:
        lines.append(
            "| Stage    | EventType                 | Total | Per-unit avg | Units with >=1 |"
        )
        lines.append(
            "|----------|---------------------------|-------|--------------|----------------|"
        )
        # Sort by total desc, then by (stage, event_type) for stability.
        sorted_rows = sorted(
            totals.items(),
            key=lambda kv: (-kv[1], kv[0][0], kv[0][1]),
        )
        for (stage, event_type), total in sorted_rows:
            per_unit_avg = total / n_units if n_units else 0.0
            n_units_with = len(units_with.get((stage, event_type), set()))
            lines.append(
                f"| {stage:<8} | {event_type:<25} | {total:>5} "
                f"| {per_unit_avg:>12.2f} | {n_units_with} / {n_units}        |"
            )
        lines.append("")

        # Recurring friction: pairs present in more than half of the units.
        half_threshold = n_units / 2 if n_units else 0
        friction = sorted(
            (
                (stage, event_type, len(units_with.get((stage, event_type), set())), total)
                for (stage, event_type), total in totals.items()
                if len(units_with.get((stage, event_type), set())) > half_threshold
            ),
            key=lambda r: (-r[2], -r[3], r[0], r[1]),
        )[:5]
        lines.append("### Recurring friction")
        lines.append("")
        if friction:
            for stage, event_type, n_with, total in friction:
                lines.append(
                    f"- `{stage}` / `{event_type}` — seen in {n_with}/{n_units} units "
                    f"({total} total occurrences)"
                )
        else:
            lines.append(
                "(no event pattern appeared in more than half the units)"
            )
        lines.append("")
    else:
        lines.append("(no event data found across the requested units)")
        lines.append("")

    # ── Retro keyword extraction ─────────────────────────────────────────
    # Count distinct units mentioning each token, then surface tokens that
    # appear in >=3 units.
    units_per_token: dict[str, set[str]] = {}
    total_per_token: Counter[str] = Counter()
    n_retros_seen = 0

    for uid in unit_ids:
        path = retro_path(project_root, uid)
        if not path.is_file():
            continue
        try:
            body = _extract_retro_sections(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not body.strip():
            continue
        n_retros_seen += 1
        tokens = _tokenize(body)
        # Build unigrams and bigrams. Bigrams from adjacent tokens only.
        ngrams: list[str] = list(tokens)
        ngrams.extend(f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False))
        unique_in_unit = set(ngrams)
        for ng in unique_in_unit:
            units_per_token.setdefault(ng, set()).add(uid)
        for ng in ngrams:
            total_per_token[ng] += 1

    lines.append("### Candidate cross-unit patterns (from retros)")
    lines.append("")
    if n_retros_seen == 0:
        lines.append("(no recurring retro keywords)")
        lines.append("")
        return "\n".join(lines)

    # Threshold: appears in >=3 units' retros.
    candidates = [
        (ng, len(units), total_per_token[ng])
        for ng, units in units_per_token.items()
        if len(units) >= 3
    ]
    if not candidates:
        lines.append("(no recurring retro keywords)")
        lines.append("")
        return "\n".join(lines)

    # Sort by (n_units desc, total desc, longer-ngram first, alpha).
    candidates.sort(key=lambda r: (-r[1], -r[2], -len(r[0]), r[0]))
    # Cap at 12; if a bigram and its constituent unigram both qualify,
    # keep both — operators can read past it.
    for ng, n_units_with_token, total in candidates[:12]:
        lines.append(f"- **{ng}** — {n_units_with_token} units, {total} mentions")
    lines.append("")
    return "\n".join(lines)
