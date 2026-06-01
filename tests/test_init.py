"""Tests for `dualpass init` (scaffolds a project from the bundled example)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from dualpass import _init, config
from dualpass.cli import main

# ── Library entrypoint ────────────────────────────────────────────────────────


def test_run_init_copies_template_into_empty_target(tmp_path: Path) -> None:
    target = tmp_path / "fresh-project"
    result = _init.run_init(target)
    assert result.target == target.resolve()
    assert result.template_name == "coding-agent"
    assert result.files_copied > 10  # the example has ~17 files
    assert (target / "config" / "dualpass.json").is_file()
    assert (target / "config" / "agents.yaml").is_file()
    assert (target / "config" / "stages.yaml").is_file()
    assert (target / "config" / "permissions.yaml").is_file()
    assert (target / "skills" / "research" / "SKILL.md").is_file()


def test_run_init_rewrites_project_name_to_target_basename(tmp_path: Path) -> None:
    target = tmp_path / "my-fancy-agent"
    _init.run_init(target)
    data = json.loads((target / "config" / "dualpass.json").read_text())
    assert data["project_name"] == "my-fancy-agent"


def test_run_init_accepts_explicit_project_name(tmp_path: Path) -> None:
    target = tmp_path / "any-dir"
    _init.run_init(target, project_name="Some Custom Name")
    data = json.loads((target / "config" / "dualpass.json").read_text())
    assert data["project_name"] == "Some Custom Name"


def test_run_init_produces_a_config_that_validates(tmp_path: Path) -> None:
    target = tmp_path / "validates"
    _init.run_init(target)
    errors = config.validate(target)
    assert errors == []


def test_run_init_creates_target_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "does" / "not" / "yet" / "exist"
    assert not target.exists()
    _init.run_init(target)
    assert target.is_dir()


def test_run_init_works_inside_an_otherwise_empty_directory(tmp_path: Path) -> None:
    # Common case: user runs `dualpass init .` inside a fresh `git init`'d dir.
    target = tmp_path / "fresh-repo"
    target.mkdir()
    (target / ".git").mkdir()  # simulate git init
    (target / ".DS_Store").write_text("noise")  # simulate macOS cruft
    _init.run_init(target)
    assert (target / "config" / "dualpass.json").is_file()


def test_run_init_refuses_to_overwrite_populated_target(tmp_path: Path) -> None:
    target = tmp_path / "already-used"
    target.mkdir()
    (target / "important-data.txt").write_text("don't lose me")
    with pytest.raises(_init.InitError, match="not empty"):
        _init.run_init(target)
    # Original file untouched.
    assert (target / "important-data.txt").read_text() == "don't lose me"


def test_run_init_skips_pycache_noise(tmp_path: Path) -> None:
    target = tmp_path / "no-pycache"
    _init.run_init(target)
    # No __pycache__ or .DS_Store in the scaffolded tree
    pycaches = list(target.rglob("__pycache__"))
    assert pycaches == []
    dsstore = list(target.rglob(".DS_Store"))
    assert dsstore == []


# ── CLI entrypoint ────────────────────────────────────────────────────────────


def test_cli_init_scaffolds_and_exits_zero(tmp_path: Path) -> None:
    target = tmp_path / "from-cli"
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["init", str(target)])
    assert rc == 0
    body = out.getvalue()
    assert "Scaffolded 'coding-agent'" in body
    assert "Next steps:" in body
    assert (target / "config" / "dualpass.json").is_file()


def test_cli_init_refuses_populated_target_and_exits_one(tmp_path: Path) -> None:
    target = tmp_path / "populated"
    target.mkdir()
    (target / "keep-me.txt").write_text("hi")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["init", str(target)])
    assert rc == 1
    assert "not empty" in err.getvalue()
    assert (target / "keep-me.txt").exists()


def test_cli_init_then_doctor_chain(tmp_path: Path) -> None:
    """End-to-end: scaffold a project, then run doctor against it. Should pass."""
    target = tmp_path / "chain"

    out = io.StringIO()
    with redirect_stdout(out):
        assert main(["init", str(target)]) == 0
    out2 = io.StringIO()
    with redirect_stdout(out2):
        assert main(["doctor", "--project", str(target)]) == 0
    assert "doctor: OK" in out2.getvalue()
