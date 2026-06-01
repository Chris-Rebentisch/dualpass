"""Smoke tests for the v0.1.0a1 scaffolding.

These tests verify the package imports cleanly, the CLI argparse surface is sane,
and stub commands emit the expected NotImplementedError signal. They do NOT exercise
unimplemented functionality.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

import dualpass
from dualpass.cli import main

# ── Package-level ──────────────────────────────────────────────────────────────


def test_version_constant_is_pep440_prerelease() -> None:
    assert dualpass.__version__ == "0.1.0a1"


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


# ── Stub behavior (commands not yet implemented in v0.1.0a1) ──────────────────


@pytest.mark.parametrize(
    "argv,expected_msg_fragment",
    [
        (["run", "--unit", "demo-001"], "'run' is not yet implemented"),
        (["init", "/tmp/x"], "'init' is not yet implemented"),
        (["status"], "'status' is not yet implemented"),
        (["retro", "--unit", "demo-001"], "'retro' is not yet implemented"),
        (["propose-dag"], "'propose-dag' is not yet implemented"),
    ],
)
def test_stub_commands_exit_two_with_structured_message(
    argv: list[str], expected_msg_fragment: str
) -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(argv)
    assert rc == 2
    assert expected_msg_fragment in err.getvalue()
    assert dualpass.__version__ in err.getvalue()  # mentions current version
    assert "CHANGELOG" in err.getvalue()  # points at the milestone tracker


def test_watcher_stub_cites_watcher_milestone() -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["watcher", "status"])
    assert rc == 2
    assert "watcher" in err.getvalue()
    assert "v0.3.0" in err.getvalue()


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


# ── Stub functions raise NotImplementedError, not silent stubs ────────────────


def test_controller_run_unit_raises_notimplemented() -> None:
    from dualpass.controller import run_unit

    with pytest.raises(NotImplementedError, match="not yet implemented"):
        run_unit("demo-001")


def test_memory_lock_present_is_implemented() -> None:
    """memory.lock_present is small enough to implement in v0.1.0a0 — verify it works."""
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
