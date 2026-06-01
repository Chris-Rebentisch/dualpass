"""Config loader + validator.

Loads `config/{dualpass.json, agents.yaml, stages.yaml, permissions.yaml}` from a
project root, validates each against a bundled JSON Schema, and returns typed
dataclasses for downstream consumption.

Public entry points:
  - load_project_config(project_root) -> ProjectConfig
  - load_agents(project_root) -> AgentsConfig
  - load_stages(project_root) -> StagesConfig
  - load_permissions(project_root) -> PermissionsConfig
  - validate(project_root) -> list[ValidationError]
  - load_all(project_root) -> LoadedConfig

`validate()` is non-raising — it returns every problem it finds so the CLI can
print them all in one shot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

# ── Public types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationError:
    """One problem found in a config file."""

    file: str
    path: str
    message: str

    def format(self) -> str:
        loc = f"{self.file}:{self.path}" if self.path else self.file
        return f"{loc}: {self.message}"


@dataclass(frozen=True)
class ProjectConfig:
    """Top-level harness config from `config/dualpass.json`."""

    version: str
    project_name: str
    unit_id_pattern: str
    max_revision_rounds: dict[str, int]
    breakpoints: dict[str, bool]
    circuit_breaker: dict[str, Any]
    single_flight_lockfile: bool
    auto_lock_finals: bool


@dataclass(frozen=True)
class AgentRole:
    """One named agent role from `config/agents.yaml`."""

    name: str
    command: str
    timeout_seconds: int | None = None
    transient_retries: int | None = None
    transient_retry_delay_seconds: int | None = None
    transient_retry_patterns: tuple[str, ...] = ()
    exhaustion_patterns: tuple[str, ...] = ()
    activate_after_consecutive_exhausted: int | None = None


@dataclass(frozen=True)
class AgentsConfig:
    """All named agent roles."""

    roles: dict[str, AgentRole]


@dataclass(frozen=True)
class StageConfig:
    """One stage in the pipeline."""

    name: str
    author_skill: str
    reviewer_skill: str | None = None
    dual_pass_reviewer: bool = False
    preflight_gates: tuple[str, ...] = ()
    max_rounds: int | None = None
    requires_predecessor: str | None = None
    breakpoint_default: bool = False


@dataclass(frozen=True)
class StagesConfig:
    """Ordered pipeline stages."""

    stages: tuple[StageConfig, ...]


@dataclass(frozen=True)
class PermissionsConfig:
    """Tiered permissions posture."""

    default_posture: str
    mutating_actions_require_approval: bool
    opt_in_skips: dict[str, bool] = field(default_factory=dict)
    forbidden_actions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    audit_log: str | None = None


@dataclass(frozen=True)
class LoadedConfig:
    """Everything load_all() returns."""

    project: ProjectConfig
    agents: AgentsConfig
    stages: StagesConfig
    permissions: PermissionsConfig


# ── Internals ─────────────────────────────────────────────────────────────────


_CONFIG_FILES: dict[str, tuple[str, str]] = {
    # logical_name -> (filename, schema_name)
    "project": ("dualpass.json", "dualpass.json"),
    "agents": ("agents.yaml", "agents.json"),
    "stages": ("stages.yaml", "stages.json"),
    "permissions": ("permissions.yaml", "permissions.json"),
}


def _config_dir(project_root: Path) -> Path:
    return project_root / "config"


def _load_schema(schema_name: str) -> dict[str, Any]:
    schema_text = (files("dualpass.schemas") / schema_name).read_text(encoding="utf-8")
    return json.loads(schema_text)


def _load_raw(config_path: Path) -> Any:
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix == ".json":
        return json.loads(text)
    if config_path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    raise ValueError(f"unsupported config extension: {config_path.suffix}")


def _format_json_path(path_parts: list[Any]) -> str:
    if not path_parts:
        return ""
    chunks: list[str] = []
    for part in path_parts:
        if isinstance(part, int):
            chunks.append(f"[{part}]")
        else:
            chunks.append(f".{part}" if chunks else str(part))
    return "".join(chunks)


def _validate_one(filename: str, schema_name: str, data: Any) -> list[ValidationError]:
    schema = _load_schema(schema_name)
    validator = Draft202012Validator(schema)
    errors: list[ValidationError] = []
    for err in validator.iter_errors(data):
        errors.append(
            ValidationError(
                file=filename,
                path=_format_json_path(list(err.absolute_path)),
                message=err.message,
            )
        )
    return errors


def _check_files_present(project_root: Path) -> list[ValidationError]:
    cfg_dir = _config_dir(project_root)
    out: list[ValidationError] = []
    if not cfg_dir.is_dir():
        out.append(
            ValidationError(
                file="config/",
                path="",
                message=f"config directory not found: {cfg_dir}",
            )
        )
        return out
    for _, (filename, _) in _CONFIG_FILES.items():
        if not (cfg_dir / filename).is_file():
            out.append(
                ValidationError(
                    file=f"config/{filename}",
                    path="",
                    message="file not found",
                )
            )
    return out


# ── Public loaders ────────────────────────────────────────────────────────────


def load_project_config(project_root: Path) -> ProjectConfig:
    """Load and validate `config/dualpass.json`."""
    filename, schema_name = _CONFIG_FILES["project"]
    path = _config_dir(project_root) / filename
    data = _load_raw(path)
    errors = _validate_one(f"config/{filename}", schema_name, data)
    if errors:
        raise ConfigError(errors)
    return ProjectConfig(
        version=data["version"],
        project_name=data["project_name"],
        unit_id_pattern=data["unit_id_pattern"],
        max_revision_rounds=dict(data["max_revision_rounds"]),
        breakpoints=dict(data["breakpoints"]),
        circuit_breaker=dict(data["circuit_breaker"]),
        single_flight_lockfile=bool(data["single_flight_lockfile"]),
        auto_lock_finals=bool(data["auto_lock_finals"]),
    )


def load_agents(project_root: Path) -> AgentsConfig:
    """Load and validate `config/agents.yaml`."""
    filename, schema_name = _CONFIG_FILES["agents"]
    path = _config_dir(project_root) / filename
    data = _load_raw(path) or {}
    errors = _validate_one(f"config/{filename}", schema_name, data)
    if errors:
        raise ConfigError(errors)
    roles: dict[str, AgentRole] = {}
    for role_name, role_data in data["roles"].items():
        roles[role_name] = AgentRole(
            name=role_name,
            command=role_data["command"],
            timeout_seconds=role_data.get("timeout_seconds"),
            transient_retries=role_data.get("transient_retries"),
            transient_retry_delay_seconds=role_data.get("transient_retry_delay_seconds"),
            transient_retry_patterns=tuple(role_data.get("transient_retry_patterns", ())),
            exhaustion_patterns=tuple(role_data.get("exhaustion_patterns", ())),
            activate_after_consecutive_exhausted=role_data.get(
                "activate_after_consecutive_exhausted"
            ),
        )
    return AgentsConfig(roles=roles)


def load_stages(project_root: Path) -> StagesConfig:
    """Load and validate `config/stages.yaml`."""
    filename, schema_name = _CONFIG_FILES["stages"]
    path = _config_dir(project_root) / filename
    data = _load_raw(path) or {}
    errors = _validate_one(f"config/{filename}", schema_name, data)
    if errors:
        raise ConfigError(errors)
    stages = tuple(
        StageConfig(
            name=s["name"],
            author_skill=s["author_skill"],
            reviewer_skill=s.get("reviewer_skill"),
            dual_pass_reviewer=bool(s.get("dual_pass_reviewer", False)),
            preflight_gates=tuple(s.get("preflight_gates", ())),
            max_rounds=s.get("max_rounds"),
            requires_predecessor=s.get("requires_predecessor"),
            breakpoint_default=bool(s.get("breakpoint_default", False)),
        )
        for s in data["stages"]
    )
    return StagesConfig(stages=stages)


def load_permissions(project_root: Path) -> PermissionsConfig:
    """Load and validate `config/permissions.yaml`."""
    filename, schema_name = _CONFIG_FILES["permissions"]
    path = _config_dir(project_root) / filename
    data = _load_raw(path) or {}
    errors = _validate_one(f"config/{filename}", schema_name, data)
    if errors:
        raise ConfigError(errors)
    forbidden = {
        category: tuple(patterns)
        for category, patterns in (data.get("forbidden_actions") or {}).items()
    }
    return PermissionsConfig(
        default_posture=data["default_posture"],
        mutating_actions_require_approval=bool(data["mutating_actions_require_approval"]),
        opt_in_skips=dict(data.get("opt_in_skips") or {}),
        forbidden_actions=forbidden,
        audit_log=data.get("audit_log"),
    )


def load_all(project_root: Path) -> LoadedConfig:
    """Load every config file. Raises ConfigError if anything fails to validate."""
    return LoadedConfig(
        project=load_project_config(project_root),
        agents=load_agents(project_root),
        stages=load_stages(project_root),
        permissions=load_permissions(project_root),
    )


def validate(project_root: Path) -> list[ValidationError]:
    """Validate every config file. Returns ALL errors found (does not raise).

    Cross-file invariants checked here:
      - every stage's `requires_predecessor` must refer to a stage defined earlier
      - every stage's `breakpoint_default`/`max_rounds` must be consistent with
        the project-level overrides
    """
    errors: list[ValidationError] = []

    file_errors = _check_files_present(project_root)
    errors.extend(file_errors)
    if file_errors:
        return errors

    # Per-file schema validation (collect all, never raise).
    for _, (filename, schema_name) in _CONFIG_FILES.items():
        path = _config_dir(project_root) / filename
        try:
            data = _load_raw(path)
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            errors.append(
                ValidationError(
                    file=f"config/{filename}",
                    path="",
                    message=f"parse error: {exc}",
                )
            )
            continue
        errors.extend(_validate_one(f"config/{filename}", schema_name, data))

    # If any schema errors landed, skip cross-file checks — they'd just add noise.
    if errors:
        return errors

    errors.extend(_cross_file_checks(project_root))
    return errors


def _cross_file_checks(project_root: Path) -> list[ValidationError]:
    out: list[ValidationError] = []
    stages = load_stages(project_root).stages
    project = load_project_config(project_root)

    # Stages: every requires_predecessor must point to an earlier-defined stage.
    seen: set[str] = set()
    for stage in stages:
        if stage.requires_predecessor is not None and stage.requires_predecessor not in seen:
            out.append(
                ValidationError(
                    file="config/stages.yaml",
                    path=f"stages[{stage.name}].requires_predecessor",
                    message=(
                        f"refers to unknown predecessor '{stage.requires_predecessor}' "
                        f"(must be a stage defined earlier in the list)"
                    ),
                )
            )
        seen.add(stage.name)

    # Project: every breakpoint key must be a real stage name.
    stage_names = {s.name for s in stages}
    for bp_name in project.breakpoints:
        if bp_name not in stage_names:
            out.append(
                ValidationError(
                    file="config/dualpass.json",
                    path=f"breakpoints.{bp_name}",
                    message=(
                        f"breakpoint refers to unknown stage '{bp_name}' "
                        f"(not declared in config/stages.yaml)"
                    ),
                )
            )

    # Project: per-stage max_revision_rounds overrides must reference real stages.
    for round_key in project.max_revision_rounds:
        if round_key == "default":
            continue
        if round_key not in stage_names:
            out.append(
                ValidationError(
                    file="config/dualpass.json",
                    path=f"max_revision_rounds.{round_key}",
                    message=(
                        f"override refers to unknown stage '{round_key}' "
                        f"(not declared in config/stages.yaml)"
                    ),
                )
            )

    return out


class ConfigError(Exception):
    """Raised by load_* helpers when validation fails. Carries the full error list."""

    def __init__(self, errors: list[ValidationError]) -> None:
        self.errors = errors
        super().__init__("; ".join(e.format() for e in errors))
