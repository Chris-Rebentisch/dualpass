"""Tests for `dualpass.memory` build-marker parser and writer.

The build-complete marker is the author-driven halt contract documented in
CONCEPTS.md §5: the author agent emits YAML frontmatter on stage exit and the
controller acts on `exit_signal`. These tests pin the parser's contract so
the controller can rely on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dualpass.memory import (
    BuildMarker,
    BuildMarkerError,
    build_marker_path,
    read_build_marker,
    state_dir,
    write_build_marker,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_marker_text(unit_id: str, project_root: Path, text: str) -> Path:
    """Drop a raw marker file on disk without going through `write_build_marker`."""
    path = state_dir(project_root) / f"{unit_id}-build-complete.md"
    path.write_text(text, encoding="utf-8")
    return path


# ── 1. File missing ──────────────────────────────────────────────────────────


def test_returns_none_when_marker_file_is_missing(tmp_path: Path) -> None:
    assert read_build_marker("demo-001", tmp_path) is None


# ── 2. Valid frontmatter ─────────────────────────────────────────────────────


def test_parses_valid_frontmatter_into_populated_buildmarker(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: complete
exit_signal: continue
blocker_kind: null
artifacts_produced:
  - units/demo-001/code-v3-FINAL.md
---
""",
    )

    marker = read_build_marker("demo-001", tmp_path)

    assert marker is not None
    assert marker.unit == "demo-001"
    assert marker.stage == "code"
    assert marker.status == "complete"
    assert marker.exit_signal == "continue"
    assert marker.blocker_kind is None
    assert marker.artifacts_produced == ["units/demo-001/code-v3-FINAL.md"]
    assert marker.metadata == {}


# ── 3. Missing opening fence ─────────────────────────────────────────────────


def test_missing_opening_triple_dash_raises(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """unit: demo-001
stage: code
status: complete
exit_signal: continue
""",
    )

    with pytest.raises(BuildMarkerError, match="opening"):
        read_build_marker("demo-001", tmp_path)


# ── 4. Unknown status value ──────────────────────────────────────────────────


def test_unknown_status_value_raises_with_field_name(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: kinda-done
exit_signal: continue
---
""",
    )

    with pytest.raises(BuildMarkerError) as excinfo:
        read_build_marker("demo-001", tmp_path)

    # The field name must appear in the message so operators can diagnose
    # without re-reading the file.
    assert "status" in str(excinfo.value)
    assert "kinda-done" in str(excinfo.value)


# ── 5. Unit mismatch ─────────────────────────────────────────────────────────


def test_unit_mismatch_raises(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: a-different-unit
stage: code
status: complete
exit_signal: continue
---
""",
    )

    with pytest.raises(BuildMarkerError, match="unit"):
        read_build_marker("demo-001", tmp_path)


# ── 6. Unknown extra field preserved in metadata ─────────────────────────────


def test_unknown_extra_field_preserved_in_metadata(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: complete
exit_signal: continue
custom_field: custom_value
nested_field:
  inner_key: inner_value
---
""",
    )

    marker = read_build_marker("demo-001", tmp_path)

    assert marker is not None
    assert marker.metadata == {
        "custom_field": "custom_value",
        "nested_field": {"inner_key": "inner_value"},
    }


# ── 7. artifacts_produced parses as list[str] ────────────────────────────────


def test_artifacts_produced_parses_as_list_of_strings(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: complete
exit_signal: continue
artifacts_produced:
  - units/demo-001/code-v1-FINAL.md
  - units/demo-001/code-v1-tests.md
  - units/demo-001/code-v1-coverage.json
---
""",
    )

    marker = read_build_marker("demo-001", tmp_path)

    assert marker is not None
    assert marker.artifacts_produced == [
        "units/demo-001/code-v1-FINAL.md",
        "units/demo-001/code-v1-tests.md",
        "units/demo-001/code-v1-coverage.json",
    ]
    assert all(isinstance(item, str) for item in marker.artifacts_produced)


def test_artifacts_produced_rejects_non_string_items(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: complete
exit_signal: continue
artifacts_produced:
  - units/demo-001/code-v1-FINAL.md
  - 42
---
""",
    )

    with pytest.raises(BuildMarkerError, match="artifacts_produced"):
        read_build_marker("demo-001", tmp_path)


# ── 8. Body after closing fence is ignored ───────────────────────────────────


def test_body_after_closing_triple_dash_is_ignored(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: complete
exit_signal: continue
---

# Build summary

This text is free-form prose that the author may include after the
frontmatter. The parser must ignore it entirely.

- bullet 1
- bullet 2
""",
    )

    marker = read_build_marker("demo-001", tmp_path)

    assert marker is not None
    assert marker.unit == "demo-001"
    assert marker.stage == "code"
    assert marker.status == "complete"
    assert marker.exit_signal == "continue"
    # The free-form prose must not bleed into metadata.
    assert marker.metadata == {}


# ── 9. Empty body (no closing fence) raises ──────────────────────────────────


def test_empty_body_without_closing_triple_dash_raises(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: complete
exit_signal: continue
""",
    )

    with pytest.raises(BuildMarkerError, match="closing"):
        read_build_marker("demo-001", tmp_path)


# ── 10. Round-trip via write_build_marker → read_build_marker ────────────────


def test_round_trip_write_then_read(tmp_path: Path) -> None:
    original = BuildMarker(
        unit="demo-042",
        stage="audit",
        status="blocked",
        exit_signal="escalate",
        blocker_kind="architectural",
        artifacts_produced=[
            "units/demo-042/audit-v2-FINAL.md",
            "units/demo-042/audit-v2-deviations.md",
        ],
        metadata={
            "reason": "Pre-existing co-tenant debt blocks clean PASS verdict",
            "review_rounds_used": 6,
        },
    )

    path = write_build_marker(original, tmp_path)
    assert path == build_marker_path("demo-042", tmp_path)
    assert path.is_file()

    round_tripped = read_build_marker("demo-042", tmp_path)

    assert round_tripped is not None
    assert round_tripped.unit == original.unit
    assert round_tripped.stage == original.stage
    assert round_tripped.status == original.status
    assert round_tripped.exit_signal == original.exit_signal
    assert round_tripped.blocker_kind == original.blocker_kind
    assert round_tripped.artifacts_produced == original.artifacts_produced
    assert round_tripped.metadata == original.metadata


# ── Bonus coverage: enum validation for the remaining two fields ─────────────


def test_unknown_exit_signal_raises_with_field_name(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: complete
exit_signal: detonate
---
""",
    )

    with pytest.raises(BuildMarkerError) as excinfo:
        read_build_marker("demo-001", tmp_path)
    assert "exit_signal" in str(excinfo.value)


def test_unknown_blocker_kind_raises_with_field_name(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
stage: code
status: blocked
exit_signal: stop
blocker_kind: cosmic-rays
---
""",
    )

    with pytest.raises(BuildMarkerError) as excinfo:
        read_build_marker("demo-001", tmp_path)
    assert "blocker_kind" in str(excinfo.value)


def test_missing_required_field_raises(tmp_path: Path) -> None:
    _write_marker_text(
        "demo-001",
        tmp_path,
        """---
unit: demo-001
status: complete
exit_signal: continue
---
""",
    )

    with pytest.raises(BuildMarkerError, match="stage"):
        read_build_marker("demo-001", tmp_path)


def test_write_refuses_invalid_status(tmp_path: Path) -> None:
    bad = BuildMarker(
        unit="demo-001",
        stage="code",
        status="kinda-done",  # type: ignore[arg-type]
        exit_signal="continue",
    )
    with pytest.raises(BuildMarkerError, match="status"):
        write_build_marker(bad, tmp_path)
