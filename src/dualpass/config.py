"""Config loader + validator.

Loads config/{dualpass.json, agents.yaml, stages.yaml, permissions.yaml} and validates
against bundled JSON schemas. Returns typed dataclasses for downstream consumption.

v0.1.0a0 status: stub. Validator + schemas land in v0.2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProjectConfig:
    """Top-level harness config from config/dualpass.json."""

    version: str
    project_name: str
    unit_id_pattern: str
    max_revision_rounds: dict[str, int]
    breakpoints: dict[str, bool]
    circuit_breaker: dict[str, Any]
    single_flight_lockfile: bool
    auto_lock_finals: bool


def load_project_config(project_root: Path) -> ProjectConfig:
    """Load and validate config/dualpass.json. Returns a ProjectConfig."""
    raise NotImplementedError("config.load_project_config — landing in v0.2.0")


def validate(project_root: Path) -> list[str]:
    """Validate all configs in project_root/config/. Returns a list of error messages
    (empty list = valid).
    """
    raise NotImplementedError("config.validate — landing in v0.2.0")
