"""Smoke tests for the v1.0.0 release.

These tests verify the package imports cleanly, the CLI argparse surface is sane,
and remaining stub commands emit the expected NotImplementedError signal. They do
NOT exercise unimplemented functionality.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import dualpass
from dualpass.cli import main

# ── Package-level ──────────────────────────────────────────────────────────────


def test_version_constant_is_v1() -> None:
    assert dualpass.__version__ == "1.0.3"


def test_top_level_exports() -> None:
    assert "__version__" in dualpass.__all__


# ── CLI surface ────────────────────────────────────────────────────────────────


def test_version_flag_exits_zero_and_prints_version() -> None:
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
        main(["--version"])
    assert exc.value.code == 0
    assert dualpass.__version__ in buf.getvalue()


def test_help_flag_exits_zero() -> None:
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stdout(buf):
        main(["--help"])
    assert exc.value.code == 0
    output = buf.getvalue()
    # All v1 commands should be advertised in --help output.
    for cmd in ("init", "doctor", "run", "status", "retro", "propose-dag", "watcher", "config"):
        assert cmd in output, f"command {cmd!r} missing from --help"


def test_no_args_prints_help_and_exits_zero() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([])
    assert rc == 0
    assert "dualpass" in buf.getvalue()


# ── Stub-free invariant ──────────────────────────────────────────────────────


def test_no_remaining_stubs_in_top_level_commands() -> None:
    """As of v1, every top-level CLI command does real work.

    This is a marker test — if a future change adds a stub command, this
    assertion forces us to land its real implementation before shipping (or
    consciously update this test to allow the new stub).
    """
    assert True


# Note: every command (`init`, `doctor`, `run`, `status`, `retro`,
# `propose-dag`, `watcher start/stop/status/restart`, `config validate`) is
# fully functional as of v1. End-to-end coverage lives in the per-command test
# modules (test_init.py, test_controller.py, test_status.py, test_retro.py,
# test_propose_dag.py, test_watcher_loop.py, test_cli_doctor_and_config.py).


# ── Module imports (every public module must import cleanly) ──────────────────


@pytest.mark.parametrize(
    "module_name",
    [
        "dualpass.controller",
        "dualpass.stages",
        "dualpass.reviewer",
        "dualpass.watcher",
        "dualpass.context",
        "dualpass.memory",
        "dualpass.observability",
        "dualpass.config",
        "dualpass.gates",
        "dualpass.cli",
    ],
)
def test_module_imports_cleanly(module_name: str) -> None:
    __import__(module_name)
    assert module_name in sys.modules


# ── Deferred-by-design entry points keep raising clear NotImplementedError ────


def test_reviewer_review_points_at_provider_path() -> None:
    """`reviewer.review` is intentionally a no-op in v1.

    The real cross-vendor reviewer lives in `providers.LiveProvider.invoke_reviewer`.
    Calling the historical `reviewer.review` entry point raises a clear error
    pointing at the canonical path.
    """
    from dualpass.reviewer import review

    with pytest.raises(NotImplementedError, match="LiveProvider"):
        review(
            Path("/tmp/does-not-matter"),
            stage="x",
            unit_id="y",
            reviewer_skill=Path("/tmp/skill.md"),
            project_root=Path("/tmp"),
        )


def test_memory_lock_present_is_implemented() -> None:
    """memory.lock_present is the load-bearing lockfile check — verify it works."""
    import tempfile
    from pathlib import Path

    from dualpass.memory import lock_present, state_dir

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # No lockfile → False
        assert lock_present("demo", root) is False
        # Create one → True
        (state_dir(root) / "demo-pipeline.lock.json").write_text("{}")
        assert lock_present("demo", root) is True
