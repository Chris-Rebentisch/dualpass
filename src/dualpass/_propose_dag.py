"""`dualpass propose-dag` implementation.

v1 explicitly ships fixed-cycle staged pipelines and stops there for DAGs.
The intent of this command is to surface design-pressure cleanly: if your
work genuinely needs a DAG (parallel stages, fan-out, conditional merges),
walk you through what one would look like and let you implement it OUTSIDE
dualpass. The harness will not become a DAG executor in v1 — the simplicity
of a fixed cycle is load-bearing.

The command asks 4 short questions, writes a markdown sketch into
`docs/_project/DAG-PROPOSAL.md`, and prints next-steps. Non-interactive mode
(`--non-interactive` with `--name`) is provided for tests + scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_DAG_DEFAULT_PATH = "docs/_project/DAG-PROPOSAL.md"


@dataclass(frozen=True)
class DagAnswers:
    """The four questions the prompt asks."""

    name: str  # short label for the proposed DAG
    parallel_stages: str  # which stages could run in parallel
    fan_out_stage: str  # which stage fans out into multiple units
    join_point: str  # where do parallel paths converge?


def proposal_path(project_root: Path) -> Path:
    return project_root / _DAG_DEFAULT_PATH


def build_proposal(answers: DagAnswers) -> str:
    """Render the markdown sketch from the operator's answers."""
    return (
        f"---\n"
        f"name: {answers.name}\n"
        f"generated: {datetime.now(UTC).date().isoformat()}\n"
        f"v1_disposition: dualpass v1 will not execute this — this is a design sketch\n"
        f"---\n\n"
        f"# DAG proposal — {answers.name}\n\n"
        f"## Why this isn't a dualpass v1 feature\n\n"
        f"dualpass v1 ships **fixed-cycle** staged pipelines (the same chain in the same "
        f"order for every unit) because that's the configuration most coding-agent "
        f"workflows actually need, and because the simplicity of the loop is what makes "
        f"the safety guarantees (single-flight lock, circuit breaker, dual-pass review) "
        f"tractable. Adding DAG execution multiplies the failure modes.\n\n"
        f"If your use case genuinely needs a DAG, this document is a sketch you can use "
        f"to implement it *outside* dualpass — typically as a shell script or Makefile "
        f"that orchestrates multiple `dualpass run` invocations.\n\n"
        f"## Your answers\n\n"
        f"### Parallel stages\n\n"
        f"> {answers.parallel_stages}\n\n"
        f"### Fan-out stage\n\n"
        f"> {answers.fan_out_stage}\n\n"
        f"### Join point\n\n"
        f"> {answers.join_point}\n\n"
        f"## Suggested implementation pattern\n\n"
        f"```sh\n"
        f"# Drive the DAG from a shell script. Each `dualpass run` is one node.\n"
        f"# Parallelism comes from `&` + `wait`. Joins come from sequencing.\n"
        f"\n"
        f"set -euo pipefail\n"
        f"\n"
        f"# Sequential prefix (everything before the fan-out)\n"
        f"dualpass run --unit {answers.name}-001\n"
        f"\n"
        f"# Fan-out (parallel)\n"
        f"dualpass run --unit {answers.name}-001-branch-a --from-stage <fan_out_stage> &\n"
        f"dualpass run --unit {answers.name}-001-branch-b --from-stage <fan_out_stage> &\n"
        f"wait\n"
        f"\n"
        f"# Join (sequential resumption with both branches' artifacts as inputs)\n"
        f"dualpass run --unit {answers.name}-001 --from-stage <join_point>\n"
        f"```\n\n"
        f"## Notes\n\n"
        f"- dualpass's `--from-stage` flag is the seam you use to express joins.\n"
        f"- Each `dualpass run` acquires its own single-flight lockfile, so parallel "
        f"runs of different unit IDs are safe.\n"
        f"- The breakpoint mechanism (`--ignore-breakpoints`) lets human review punctuate "
        f"the DAG without DAG-native support.\n"
    )


def write_proposal(project_root: Path, answers: DagAnswers) -> Path:
    """Write the proposal to docs/_project/DAG-PROPOSAL.md. Returns the path."""
    path = proposal_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_proposal(answers), encoding="utf-8")
    return path


def _prompt(question: str, default: str = "") -> str:
    """Wrap input() with a default fallback so EOF or empty input doesn't crash."""
    try:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{question}{suffix}: ").strip()
    except EOFError:
        return default
    return raw or default


def run_interactive(project_root: Path) -> Path:
    """Walk the operator through the four questions, write the proposal, return path."""
    print(
        "dualpass v1 ships fixed-cycle pipelines and stops there for DAGs.\n"
        "This walkthrough produces a sketch you can implement *outside* dualpass.\n"
    )
    name = _prompt("name for the proposal (short slug)", "dag-proposal")
    parallel = _prompt("which stages could run in parallel?", "(describe)")
    fan_out = _prompt("which stage fans out into multiple units?", "(describe)")
    join = _prompt("where do the parallel paths converge?", "(describe)")
    answers = DagAnswers(
        name=name, parallel_stages=parallel, fan_out_stage=fan_out, join_point=join
    )
    return write_proposal(project_root, answers)
