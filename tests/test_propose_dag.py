"""Tests for `dualpass propose-dag`."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from dualpass import _propose_dag
from dualpass.cli import main


def test_build_proposal_includes_all_answers() -> None:
    answers = _propose_dag.DagAnswers(
        name="my-dag",
        parallel_stages="A and B run in parallel after research",
        fan_out_stage="research fans out into per-source units",
        join_point="rejoins at the audit stage",
    )
    body = _propose_dag.build_proposal(answers)
    assert "my-dag" in body
    assert "A and B run in parallel" in body
    assert "fans out into per-source units" in body
    assert "rejoins at the audit stage" in body
    # The "not a v1 feature" framing is always present.
    assert "fixed-cycle" in body
    assert "outside* dualpass" in body  # italicized in the rendered text


def test_write_proposal_creates_default_path(tmp_path: Path) -> None:
    answers = _propose_dag.DagAnswers(
        name="x", parallel_stages="a", fan_out_stage="b", join_point="c"
    )
    path = _propose_dag.write_proposal(tmp_path, answers)
    assert path == tmp_path / "docs" / "_project" / "DAG-PROPOSAL.md"
    assert path.is_file()


def test_cli_propose_dag_non_interactive_writes_file(tmp_path: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(
            [
                "propose-dag",
                "--project",
                str(tmp_path),
                "--non-interactive",
                "--name",
                "smoke-dag",
                "--parallel",
                "stage1 stage2",
                "--fan-out",
                "stage3",
                "--join",
                "stage4",
            ]
        )
    assert rc == 0
    assert "DAG proposal written" in out.getvalue()
    path = tmp_path / "docs" / "_project" / "DAG-PROPOSAL.md"
    assert path.is_file()
    body = path.read_text()
    assert "smoke-dag" in body
    assert "stage1 stage2" in body
    assert "stage3" in body
    assert "stage4" in body


def test_cli_propose_dag_default_non_interactive_values_still_write(tmp_path: Path) -> None:
    """Even without --parallel/--fan-out/--join, --non-interactive should succeed with defaults."""
    out = io.StringIO()
    with redirect_stdout(out):
        rc = main(["propose-dag", "--project", str(tmp_path), "--non-interactive"])
    assert rc == 0
    path = tmp_path / "docs" / "_project" / "DAG-PROPOSAL.md"
    assert path.is_file()
    # Default name slug.
    assert "dag-proposal" in path.read_text()
