"""Tests for the functional CLI commands (doctor + config validate) landed in v0.1.0a1."""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from dualpass.cli import main

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "coding-agent"


@pytest.fixture
def good_project(tmp_path: Path) -> Path:
    dest = tmp_path / "project"
    shutil.copytree(EXAMPLE, dest)
    return dest


# ── dualpass config validate ─────────────────────────────────────────────────


def test_config_validate_passes_on_bundled_example(good_project: Path) -> None:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(["config", "validate", "--project", str(good_project)])
    assert rc == 0
    assert "valid" in out.getvalue()


def test_config_validate_reports_errors_and_exits_one(good_project: Path) -> None:
    # Break the config in a way the validator must catch.
    dp_path = good_project / "config" / "dualpass.json"
    data = json.loads(dp_path.read_text())
    data["circuit_breaker"]["progress_signal"] = "BOGUS"
    dp_path.write_text(json.dumps(data))

    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["config", "validate", "--project", str(good_project)])
    assert rc == 1
    assert "circuit_breaker" in err.getvalue()


def test_config_validate_reports_missing_config_dir(tmp_path: Path) -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["config", "validate", "--project", str(tmp_path)])
    assert rc == 1
    assert "no config/ directory" in err.getvalue()


# ── dualpass doctor ──────────────────────────────────────────────────────────


def test_doctor_succeeds_on_good_project(good_project: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["doctor", "--project", str(good_project)])
    assert rc == 0
    body = out.getvalue()
    assert "doctor: OK" in body
    assert "python:" in body
    assert "agent CLIs:" in body
    assert "config: valid" in body


def test_doctor_reports_invalid_config_and_exits_one(good_project: Path) -> None:
    (good_project / "config" / "agents.yaml").write_text("roles:\n  author:\n")  # missing command
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(["doctor", "--project", str(good_project)])
    assert rc == 1
    assert "doctor: FAIL" in err.getvalue()


def test_doctor_runs_without_config_dir(tmp_path: Path) -> None:
    """A fresh directory with no config/ is informational, not an error.

    The state dir is writable and the CLIs may or may not be installed, but the
    absence of a config tree alone shouldn't fail — that's what `dualpass init`
    is for.
    """
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["doctor", "--project", str(tmp_path)])
    # Exit code can be 0 (state dir writable, no config to fail on) or 1 if a
    # downstream check (e.g. CLI presence — though we don't fail on those here)
    # complains. The contract: rc reflects only the writability + config check.
    assert rc == 0
    body = out.getvalue()
    assert "no config/ directory" in body
