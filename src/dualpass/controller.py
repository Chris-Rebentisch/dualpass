"""The deterministic loop driver — owns control flow, retries, single-flight, circuit breaker.

This module is the load-bearing surface of dualpass. Changes here require prior discussion
per CONTRIBUTING.md.

v0.1.0a0 status: stub. The implementation lands incrementally over v0.1.0-v0.2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

StageStatus = Literal["pending", "in_flight", "complete", "blocked", "skipped"]
ExitSignal = Literal["stop", "continue", "escalate"]


@dataclass
class StageResult:
    """Outcome of a single stage invocation."""

    stage: str
    status: StageStatus
    exit_signal: ExitSignal
    artifact_path: Path | None
    rounds_used: int
    blocker_kind: str | None = None
    blocker_detail: str | None = None


def run_unit(
    unit_id: str,
    *,
    from_stage: str | None = None,
    provider: Literal["live", "mock"] = "live",
    project_root: Path | None = None,
) -> int:
    """Run a single unit through the configured stage chain.

    Returns the process exit code (0 = success, non-zero = failure).
    """
    raise NotImplementedError(
        "controller.run_unit is not yet implemented. "
        "v0.1.0a0 is a scaffolding release — see CHANGELOG.md for what's planned."
    )
