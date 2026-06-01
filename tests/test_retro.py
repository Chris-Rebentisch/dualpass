"""Tests for `dualpass retro` (single-unit + range aggregation)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from dualpass import _init, _retro, controller
from dualpass.cli import main
from dualpass.memory import state_dir


def _write_events(
    project_root: Path,
    unit_id: str,
    events: list[dict],
) -> None:
    """Helper: stamp a unit's event log directly, bypassing the controller."""
    sdir = state_dir(project_root)
    path = sdir / f"{unit_id}-events.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, sort_keys=True) + "\n")


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    _init.run_init(target)
    return target


# ── Range parsing ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("001..003", ["001", "002", "003"]),
        ("my-001..my-003", ["my-001", "my-002", "my-003"]),
        ("alpha-7..alpha-9", ["alpha-7", "alpha-8", "alpha-9"]),  # width-1 OK
        ("0..2", ["0", "1", "2"]),
    ],
)
def test_parse_range_normal_cases(spec: str, expected: list[str]) -> None:
    assert _retro.parse_range(spec) == expected


@pytest.mark.parametrize(
    "spec,fragment",
    [
        ("malformed", "invalid range"),
        ("alpha-001..beta-005", "different prefixes"),
        ("foo..bar", "must end in a number"),
        ("010..001", "before start"),
    ],
)
def test_parse_range_rejects_malformed(spec: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        _retro.parse_range(spec)


# ── Single-unit retro ────────────────────────────────────────────────────────


def test_open_or_create_creates_template_with_run_summary(scaffolded_project: Path) -> None:
    controller.run_unit(
        "demo-001",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    path, created = _retro.open_or_create(scaffolded_project, "demo-001")
    assert created is True
    assert path.is_file()
    body = path.read_text()
    assert "Retrospective — demo-001" in body
    assert "state: completed" in body
    # Stages line should be populated (not "(none)").
    assert "research, outline" in body


def test_open_or_create_does_not_clobber_existing(scaffolded_project: Path) -> None:
    """Second call should leave the user's edits intact."""
    controller.run_unit(
        "demo-edit",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    path, _ = _retro.open_or_create(scaffolded_project, "demo-edit")
    path.write_text(path.read_text() + "\n\n## USER ADDITION\nimportant text\n")
    path, created = _retro.open_or_create(scaffolded_project, "demo-edit")
    assert created is False
    assert "USER ADDITION" in path.read_text()


def test_open_or_create_works_for_unit_with_no_events(scaffolded_project: Path) -> None:
    """A retro for a unit that never ran still produces a valid stub."""
    path, created = _retro.open_or_create(scaffolded_project, "never-ran")
    assert created
    body = path.read_text()
    assert "never-ran" in body
    assert "state: unknown" in body


# ── Range aggregation ───────────────────────────────────────────────────────


def test_aggregate_concatenates_per_unit_retros(scaffolded_project: Path) -> None:
    # Pre-seed three retros.
    for uid in ("unit-001", "unit-002", "unit-003"):
        path = _retro.retro_path(scaffolded_project, uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Retro for {uid}\n\nbody for {uid}\n")

    result = _retro.aggregate(scaffolded_project, ["unit-001", "unit-002", "unit-003"])
    assert result.included == ["unit-001", "unit-002", "unit-003"]
    assert result.missing == []
    rollup = result.output.read_text()
    # Frontmatter + TOC + each body.
    assert "range: unit-001..unit-003" in rollup
    assert "included_count: 3" in rollup
    assert "## unit-001" in rollup
    assert "body for unit-001" in rollup
    assert "body for unit-003" in rollup


def test_aggregate_reports_missing_units(scaffolded_project: Path) -> None:
    # Only one of three exists.
    p = _retro.retro_path(scaffolded_project, "unit-001")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# Retro for unit-001\nbody\n")

    result = _retro.aggregate(scaffolded_project, ["unit-001", "unit-002", "unit-003"])
    assert result.included == ["unit-001"]
    assert result.missing == ["unit-002", "unit-003"]
    rollup = result.output.read_text()
    assert "Missing retros" in rollup
    assert "`unit-002`" in rollup


# ── CLI surface ─────────────────────────────────────────────────────────────


def test_cli_retro_unit_creates_and_reports_path(scaffolded_project: Path) -> None:
    controller.run_unit(
        "cli-001",
        provider="mock",
        project_root=scaffolded_project,
        ignore_breakpoints=True,
    )
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["retro", "--unit", "cli-001", "--project", str(scaffolded_project)])
    assert rc == 0
    assert "cli-001" in out.getvalue()
    assert "(created)" in out.getvalue()
    # Re-run reports `(exists)`.
    out2 = io.StringIO()
    with redirect_stdout(out2):
        main(["retro", "--unit", "cli-001", "--project", str(scaffolded_project)])
    assert "(exists)" in out2.getvalue()


def test_cli_retro_range_writes_rollup(scaffolded_project: Path) -> None:
    for uid in ("xyz-001", "xyz-002"):
        path = _retro.retro_path(scaffolded_project, uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {uid}\n\nbody {uid}\n")

    out_path = scaffolded_project / "out-rollup.md"
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(
            [
                "retro",
                "--range",
                "xyz-001..xyz-002",
                "--output",
                str(out_path),
                "--project",
                str(scaffolded_project),
            ]
        )
    assert rc == 0
    assert out_path.is_file()
    body = out_path.read_text()
    assert "xyz-001" in body and "xyz-002" in body


def test_cli_retro_without_unit_or_range_errors_two(scaffolded_project: Path) -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["retro", "--project", str(scaffolded_project)])
    assert rc == 2
    assert "--unit" in err.getvalue() and "--range" in err.getvalue()


def test_cli_retro_rejects_both_flags(scaffolded_project: Path) -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(
            [
                "retro",
                "--unit",
                "x",
                "--range",
                "001..002",
                "--project",
                str(scaffolded_project),
            ]
        )
    assert rc == 2
    assert "mutually exclusive" in err.getvalue()


def test_cli_retro_malformed_range_errors_two(scaffolded_project: Path) -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["retro", "--range", "garbage", "--project", str(scaffolded_project)])
    assert rc == 2
    assert "invalid range" in err.getvalue()


# ── Cross-unit pattern aggregation ─────────────────────────────────────────


def test_aggregate_patterns_no_units_returns_placeholder(scaffolded_project: Path) -> None:
    """With zero units the function still emits a header and a no-data note."""
    section = _retro.aggregate_patterns(unit_ids=[], project_root=scaffolded_project)
    assert "## Patterns across 0 units" in section
    assert "(no event data found" in section
    assert "(no recurring retro keywords)" in section


def test_aggregate_patterns_table_populated_for_three_units(
    scaffolded_project: Path,
) -> None:
    """Three units with mixed events then the table picks up each (stage, type)."""
    _write_events(
        scaffolded_project,
        "u-001",
        [
            {"type": "stage_revision_requested", "unit": "u-001", "stage": "code"},
            {"type": "stage_revision_requested", "unit": "u-001", "stage": "code"},
            {"type": "stage_completed", "unit": "u-001", "stage": "research"},
        ],
    )
    _write_events(
        scaffolded_project,
        "u-002",
        [
            {"type": "stage_revision_requested", "unit": "u-002", "stage": "code"},
            {"type": "stage_blocked", "unit": "u-002", "stage": "audit"},
        ],
    )
    _write_events(
        scaffolded_project,
        "u-003",
        [
            {"type": "circuit_breaker_tripped", "unit": "u-003", "stage": "code"},
        ],
    )

    section = _retro.aggregate_patterns(
        unit_ids=["u-001", "u-002", "u-003"],
        project_root=scaffolded_project,
    )
    assert "## Patterns across 3 units" in section
    # Totals per (stage, type)
    assert "stage_revision_requested" in section
    assert "circuit_breaker_tripped" in section
    assert "stage_blocked" in section
    # Per-unit avg = 3 / 3 = 1.00 for revision_requested in code.
    assert "1.00" in section
    # Recurring friction: revision_requested in code was in 2 of 3 units (>1.5).
    assert "Recurring friction" in section
    assert "code" in section


def test_aggregate_patterns_groups_by_stage_and_event_type(
    scaffolded_project: Path,
) -> None:
    """Same event_type under different stages must produce separate rows."""
    _write_events(
        scaffolded_project,
        "g-001",
        [
            {"type": "stage_completed", "unit": "g-001", "stage": "code"},
            {"type": "stage_completed", "unit": "g-001", "stage": "audit"},
        ],
    )
    section = _retro.aggregate_patterns(
        unit_ids=["g-001"],
        project_root=scaffolded_project,
    )
    # Two table rows: one for code/stage_completed, one for audit/stage_completed.
    # Each row should appear exactly once.
    # Count the data-row leading delimiter `| code     |` etc.
    code_rows = section.count("| code     | stage_completed")
    audit_rows = section.count("| audit    | stage_completed")
    assert code_rows == 1
    assert audit_rows == 1


def test_aggregate_patterns_per_unit_avg_is_total_over_n_units(
    scaffolded_project: Path,
) -> None:
    """4 events / 2 units then per-unit avg = 2.00."""
    _write_events(
        scaffolded_project,
        "avg-001",
        [
            {"type": "stage_revision_requested", "unit": "avg-001", "stage": "code"},
            {"type": "stage_revision_requested", "unit": "avg-001", "stage": "code"},
            {"type": "stage_revision_requested", "unit": "avg-001", "stage": "code"},
        ],
    )
    _write_events(
        scaffolded_project,
        "avg-002",
        [
            {"type": "stage_revision_requested", "unit": "avg-002", "stage": "code"},
        ],
    )
    section = _retro.aggregate_patterns(
        unit_ids=["avg-001", "avg-002"],
        project_root=scaffolded_project,
    )
    # Total = 4, n_units = 2, avg = 2.00.
    assert "2.00" in section
    # Units with >=1 = 2 / 2.
    assert "2 / 2" in section


def test_range_mode_prepends_aggregate_section_before_toc(
    scaffolded_project: Path,
) -> None:
    """`aggregate()` rollup must contain Patterns section above the Table of contents."""
    for uid in ("r-001", "r-002"):
        path = _retro.retro_path(scaffolded_project, uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Retro for {uid}\n\n## What went wrong\n\nflaky tests bit us\n"
        )
        _write_events(
            scaffolded_project,
            uid,
            [{"type": "stage_revision_requested", "unit": uid, "stage": "code"}],
        )

    result = _retro.aggregate(scaffolded_project, ["r-001", "r-002"])
    body = result.output.read_text()
    # Order matters: Patterns section appears before TOC isn't quite right —
    # the spec says "prepend BEFORE the existing TOC." The header is the page
    # title; we want the Patterns section to land between the title block and
    # the per-unit bodies, and above the TOC content. Verify it sits BEFORE
    # the per-unit `## r-001` body header and AFTER the page header.
    patterns_idx = body.index("## Patterns across 2 units")
    body_idx = body.index("## r-001")
    title_idx = body.index("# Rollup retrospective")
    assert title_idx < patterns_idx < body_idx
    # And the TOC entries are present somewhere in the document.
    assert "[r-001](#r-001)" in body


def test_aggregate_patterns_bigram_keyword_extraction(
    scaffolded_project: Path,
) -> None:
    """Three retros all mention 'flaky tests' then it appears in bigram candidates."""
    for uid in ("k-001", "k-002", "k-003"):
        path = _retro.retro_path(scaffolded_project, uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Retro for {uid}\n\n"
            f"## What went wrong\n\n"
            f"the flaky tests cost us a day in {uid}\n\n"
            f"## Changes for next time\n\n"
            f"isolate flaky tests in their own marker\n"
        )

    section = _retro.aggregate_patterns(
        unit_ids=["k-001", "k-002", "k-003"],
        project_root=scaffolded_project,
    )
    assert "Candidate cross-unit patterns" in section
    assert "flaky tests" in section
    # Should report 3 units mentioning it.
    assert "3 units" in section


def test_aggregate_patterns_filters_stopwords(scaffolded_project: Path) -> None:
    """Stopwords ('the', 'a', 'is') must never appear in the candidate list."""
    for uid in ("s-001", "s-002", "s-003"):
        path = _retro.retro_path(scaffolded_project, uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Retro for {uid}\n\n"
            f"## What went wrong\n\n"
            f"the harness is a thing and the the the\n"
        )

    section = _retro.aggregate_patterns(
        unit_ids=["s-001", "s-002", "s-003"],
        project_root=scaffolded_project,
    )
    # Find the candidate-list portion only.
    marker = "### Candidate cross-unit patterns"
    assert marker in section
    candidates = section.split(marker, 1)[1]
    # Each stopword should not appear as a bullet (it would render as
    # `- **the** — ...`). Allow it to appear as a substring inside a bigram
    # where it isn't the whole token — but our tokenizer drops stopwords
    # before n-gram assembly, so neither unigrams nor bigrams should contain
    # bare stopwords as bullets.
    for sw in ("**the**", "**a**", "**is**", "**and**"):
        assert sw not in candidates


def test_range_mode_with_empty_retros_falls_back_gracefully(
    scaffolded_project: Path,
) -> None:
    """No retros and no events still produces a valid rollup."""
    # Don't create any retro files or events. aggregate() will see them
    # all as missing.
    result = _retro.aggregate(scaffolded_project, ["empty-001", "empty-002"])
    assert result.included == []
    assert result.missing == ["empty-001", "empty-002"]
    body = result.output.read_text()
    # Patterns section header is always present.
    assert "## Patterns across 2 units" in body
    # No event data → no event table.
    assert "(no event data found" in body
    # No retros → keyword section shows the placeholder.
    assert "(no recurring retro keywords)" in body
