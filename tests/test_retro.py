"""Tests for `dualpass retro` (single-unit + range aggregation)."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from dualpass import _init, _retro, controller
from dualpass.cli import main


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
