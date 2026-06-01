"""Stage abstraction — load + validate `config/stages.yaml`, resolve skills.

v0.1.0a0 status: stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class StageDef:
    """One stage in the configured pipeline."""

    name: str
    author_skill: Path
    reviewer_skill: Path
    dual_pass_reviewer: bool = False
    preflight_gates: list[str] | None = None
    max_rounds: int | None = None
    requires_predecessor: str | None = None
    breakpoint_default: bool = False


def load_stages(stages_yaml: Path) -> list[StageDef]:
    """Load and validate the configured stage chain."""
    raise NotImplementedError("stages.load_stages — landing in v0.2.0")
