"""Smoke tests for the v0.2.0a0 release.

These tests verify the package imports cleanly, the CLI argparse surface is sane,
and remaining stub commands emit the expected NotImplementedError signal. They do
NOT exercise unimplemented functionality.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

import dualpass
from dualpass.cli import main

# ── Package-level ──────────────────────────────────────────────────────────────


def test_version_constant_is_pep440_prerelease() -> None:
    assert dualpass.__version__ == "0.2.0a1"


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


def test_watcher_start_stub_cites_watcher_milestone() -> None:
    """`watcher status` and `watcher stop` are now implemented; only `start`
    and `restart` remain as stubs. They should cite the v0.3.0 milestone."""
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main(["watcher", "start", "research"])
    assert rc == 2
    assert "watcher start" in err.getvalue()
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


def test_reviewer_invoke_raises_notimplemented() -> None:
    """reviewer.review is not yet wired — landing in v0.3.0+."""
    from dualpass.reviewer import review

    with pytest.raises(NotImplementedError, match="not yet wired"):
        review(
            Path("/tmp/does-not-matter"),
            stage="x",
            unit_id="y",
            reviewer_skill=Path("/tmp/skill.md"),
            project_root=Path("/tmp"),
        )


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
