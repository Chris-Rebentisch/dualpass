"""Tests for `dualpass.context` (stage-context bundle + precedent cache builders)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from dualpass.context import build_precedent_cache, build_stage_context

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> Path:
    """Write `content` to `path`, creating parents. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _populate_project_docs(project_root: Path) -> None:
    """Drop the four canonical project docs into `<root>/docs/_project/`."""
    docs_dir = project_root / "docs" / "_project"
    _write(docs_dir / "PROJECT.md", "# Project\n\nProject overview body.\n")
    _write(
        docs_dir / "DECISIONS.md",
        "# Decisions\n\n- decision-001: pick option A.\n",
    )
    _write(docs_dir / "BACKLOG.md", "# Backlog\n\n- unit-002: stretch goal.\n")
    _write(docs_dir / "DOC-MAP.md", "# Doc Map\n\n- PROJECT.md is canonical.\n")


def _touch_with_mtime(path: Path, mtime: float) -> None:
    """Force a deterministic mtime so 'newest' comparisons are reliable in tests."""
    os.utime(path, (mtime, mtime))


# ── build_stage_context ──────────────────────────────────────────────────────


def test_stage_context_renders_all_four_project_docs(tmp_path: Path) -> None:
    _populate_project_docs(tmp_path)
    out = build_stage_context(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
    )

    assert out == (tmp_path / ".dualpass-state" / "unit-001-stage-context.md").resolve()
    bundle = out.read_text(encoding="utf-8")

    # Each section header should appear, and the body content from each doc
    # should be carried through.
    assert "## PROJECT.md (first 60 lines, truncated)" in bundle
    assert "## DECISIONS.md (first 80 lines, truncated)" in bundle
    assert "## BACKLOG.md (first 40 lines, truncated)" in bundle
    assert "## DOC-MAP.md (first 40 lines, truncated)" in bundle
    assert "Project overview body." in bundle
    assert "decision-001" in bundle
    assert "unit-002" in bundle
    assert "PROJECT.md is canonical." in bundle


def test_stage_context_marks_missing_project_doc_as_not_found(tmp_path: Path) -> None:
    _populate_project_docs(tmp_path)
    # Remove PROJECT.md so its slot must be marked "(not found)".
    (tmp_path / "docs" / "_project" / "PROJECT.md").unlink()

    out = build_stage_context(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
    )
    bundle = out.read_text(encoding="utf-8")

    # PROJECT.md section is present but its body is the "(not found)" marker.
    assert "## PROJECT.md (first 60 lines, truncated)" in bundle
    project_section = bundle.split("## PROJECT.md")[1].split("## ")[0]
    assert "(not found)" in project_section

    # Other docs are still rendered with content.
    assert "decision-001" in bundle
    assert "unit-002" in bundle
    assert "PROJECT.md is canonical." in bundle


def test_stage_context_picks_up_predecessor_final(tmp_path: Path) -> None:
    _populate_project_docs(tmp_path)
    pred_dir = tmp_path / ".dualpass-state" / "unit-001"
    _write(
        pred_dir / "research-v1-FINAL.md",
        "# Research FINAL\n\nFinal research body for unit-001.\n",
    )

    out = build_stage_context(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
        predecessor_stage="research",
    )
    bundle = out.read_text(encoding="utf-8")

    assert "Predecessor stage: research" in bundle
    assert "research-v1-FINAL.md" in bundle
    assert "Final research body for unit-001." in bundle


def test_stage_context_falls_back_to_non_final_predecessor(tmp_path: Path) -> None:
    _populate_project_docs(tmp_path)
    pred_dir = tmp_path / ".dualpass-state" / "unit-001"
    # Only a draft exists — no FINAL.
    _write(
        pred_dir / "research-v2.md",
        "# Research draft v2\n\nDraft body, not yet ratified.\n",
    )

    out = build_stage_context(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
        predecessor_stage="research",
    )
    bundle = out.read_text(encoding="utf-8")

    assert "research-v2.md" in bundle
    assert "Draft body, not yet ratified." in bundle


def test_stage_context_no_predecessor_marks_section_explicitly(tmp_path: Path) -> None:
    _populate_project_docs(tmp_path)
    out = build_stage_context(
        unit_id="unit-001",
        stage="research",
        project_root=tmp_path,
        # predecessor_stage left as default None.
    )
    bundle = out.read_text(encoding="utf-8")

    # Header surfaces the predecessor as "(none)" and the predecessor section
    # carries the "(no predecessor stage)" marker.
    assert "predecessor_stage: (none)" in bundle
    assert "(no predecessor stage)" in bundle


def test_stage_context_writes_file_atomically_at_expected_path(tmp_path: Path) -> None:
    _populate_project_docs(tmp_path)
    out = build_stage_context(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
    )
    # The file must exist at the expected location, and no `.tmp` sidecar
    # should be left lying around after a successful write.
    assert out.is_file()
    assert not out.with_suffix(out.suffix + ".tmp").exists()
    assert out.parent.name == ".dualpass-state"


# ── build_precedent_cache ────────────────────────────────────────────────────


def test_precedent_cache_picks_newest_three_of_five_peers(tmp_path: Path) -> None:
    state_root = tmp_path / ".dualpass-state"

    # Five peer units each with a FINAL outline. Use deterministic mtimes so
    # we know which three should win.
    peer_specs = [
        ("unit-010", 1_700_000_100),
        ("unit-011", 1_700_000_200),
        ("unit-012", 1_700_000_300),
        ("unit-013", 1_700_000_400),
        ("unit-014", 1_700_000_500),
    ]
    for peer_id, mtime in peer_specs:
        peer_file = _write(
            state_root / peer_id / "outline-v1-FINAL.md",
            f"# Outline FINAL for {peer_id}\n\nBody for {peer_id}.\n",
        )
        _touch_with_mtime(peer_file, mtime)

    out = build_precedent_cache(
        unit_id="unit-current",
        stage="outline",
        project_root=tmp_path,
    )
    cache = out.read_text(encoding="utf-8")

    # Newest three (014, 013, 012) win; oldest two (010, 011) excluded.
    assert "unit-014" in cache
    assert "unit-013" in cache
    assert "unit-012" in cache
    assert "unit-010" not in cache
    assert "unit-011" not in cache

    # Bodies are carried through, not just filenames.
    assert "Body for unit-014." in cache
    assert "Body for unit-013." in cache
    assert "Body for unit-012." in cache


def test_precedent_cache_writes_marker_when_no_peers_exist(tmp_path: Path) -> None:
    # `.dualpass-state/` doesn't exist yet — builder must still write the cache.
    out = build_precedent_cache(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
    )
    assert out.is_file()
    cache = out.read_text(encoding="utf-8")
    assert "(no precedent units found)" in cache


def test_precedent_cache_ignores_current_units_own_final(tmp_path: Path) -> None:
    state_root = tmp_path / ".dualpass-state"

    # Current unit has its own FINAL — it must NOT appear in its own cache.
    current_file = _write(
        state_root / "unit-current" / "outline-v1-FINAL.md",
        "# Outline FINAL for unit-current\n\nSelf body.\n",
    )
    _touch_with_mtime(current_file, 1_700_001_000)

    # One genuine peer to make sure the cache isn't just empty.
    peer_file = _write(
        state_root / "unit-peer" / "outline-v1-FINAL.md",
        "# Outline FINAL for unit-peer\n\nPeer body.\n",
    )
    _touch_with_mtime(peer_file, 1_700_000_500)

    out = build_precedent_cache(
        unit_id="unit-current",
        stage="outline",
        project_root=tmp_path,
    )
    cache = out.read_text(encoding="utf-8")

    assert "Peer body." in cache
    assert "Self body." not in cache
    assert "unit-peer" in cache
    # `unit-current` will appear in the header but never as a peer section.
    peer_sections = cache.split("## Peer unit:")
    # Header (index 0) may mention unit-current; peer sections (index 1+) must not.
    for section in peer_sections[1:]:
        assert "unit-current" not in section


# ── Determinism sanity check ──────────────────────────────────────────────────
# Not in the spec's required list, but cheap to add: rerunning the builder on
# unchanged inputs should produce identical bytes. Catches accidental clock
# reads or unordered set iteration.


def test_stage_context_is_deterministic_across_runs(tmp_path: Path) -> None:
    _populate_project_docs(tmp_path)
    out1 = build_stage_context(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
    )
    content1 = out1.read_bytes()
    time.sleep(0.01)  # ensure any wall-clock-based bug would observe a tick
    build_stage_context(
        unit_id="unit-001",
        stage="outline",
        project_root=tmp_path,
    )
    content2 = out1.read_bytes()
    assert content1 == content2
