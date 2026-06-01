"""Tests for the config loader + validator."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dualpass.config import (
    ConfigError,
    load_agents,
    load_all,
    load_permissions,
    load_project_config,
    load_stages,
    validate,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "coding-agent"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def good_project(tmp_path: Path) -> Path:
    """Bundled `examples/coding-agent` copied into a tmpdir."""
    dest = tmp_path / "project"
    shutil.copytree(EXAMPLE, dest)
    return dest


# ── Happy-path loaders ────────────────────────────────────────────────────────


def test_load_project_config_returns_typed_dataclass(good_project: Path) -> None:
    cfg = load_project_config(good_project)
    assert cfg.project_name == "coding-agent (example)"
    assert cfg.max_revision_rounds["default"] == 6
    assert cfg.max_revision_rounds["spec"] == 8
    assert cfg.single_flight_lockfile is True
    assert cfg.circuit_breaker["max_no_progress_relaunches"] == 3


def test_load_agents_includes_reviewer_fallback(good_project: Path) -> None:
    cfg = load_agents(good_project)
    assert "author" in cfg.roles
    assert "reviewer" in cfg.roles
    assert "reviewer_fallback" in cfg.roles
    fb = cfg.roles["reviewer_fallback"]
    assert fb.activate_after_consecutive_exhausted == 3
    assert "\\[resource_exhausted\\]" in fb.exhaustion_patterns


def test_load_stages_returns_seven_stages_in_order(good_project: Path) -> None:
    cfg = load_stages(good_project)
    names = [s.name for s in cfg.stages]
    assert names == ["research", "outline", "spec", "prompt", "code", "audit", "handoff"]
    # spec stage uses dual-pass review
    spec = next(s for s in cfg.stages if s.name == "spec")
    assert spec.dual_pass_reviewer is True
    assert spec.max_rounds == 8
    # code stage has no reviewer (audit is the next stage)
    code = next(s for s in cfg.stages if s.name == "code")
    assert code.reviewer_skill is None
    assert code.breakpoint_default is True


def test_load_permissions_returns_typed_dataclass(good_project: Path) -> None:
    cfg = load_permissions(good_project)
    assert cfg.default_posture == "ask"
    assert cfg.mutating_actions_require_approval is True
    assert "destructive_git" in cfg.forbidden_actions
    assert cfg.audit_log == ".dualpass-state/permission-audit.log"


def test_load_all_returns_every_section(good_project: Path) -> None:
    loaded = load_all(good_project)
    assert loaded.project.project_name.startswith("coding-agent")
    assert "author" in loaded.agents.roles
    assert len(loaded.stages.stages) == 7
    assert loaded.permissions.default_posture == "ask"


# ── Validator on the happy path ──────────────────────────────────────────────


def test_validate_returns_empty_for_bundled_example(good_project: Path) -> None:
    assert validate(good_project) == []


# ── Validator catches missing files ──────────────────────────────────────────


def test_validate_reports_missing_config_dir(tmp_path: Path) -> None:
    errs = validate(tmp_path)
    assert len(errs) == 1
    assert "config directory not found" in errs[0].message


def test_validate_reports_missing_individual_files(good_project: Path) -> None:
    (good_project / "config" / "agents.yaml").unlink()
    errs = validate(good_project)
    paths = [e.file for e in errs]
    assert "config/agents.yaml" in paths


# ── Validator catches schema violations ──────────────────────────────────────


def test_validate_reports_invalid_project_field(good_project: Path) -> None:
    dp_path = good_project / "config" / "dualpass.json"
    data = json.loads(dp_path.read_text())
    data["circuit_breaker"]["progress_signal"] = "not-a-real-signal"
    dp_path.write_text(json.dumps(data))
    errs = validate(good_project)
    assert any("circuit_breaker" in e.path for e in errs)


def test_validate_reports_invalid_stage_name(good_project: Path) -> None:
    stages_path = good_project / "config" / "stages.yaml"
    stages_path.write_text(
        "stages:\n"
        "  - name: BadStageName\n"  # capital letters violate pattern
        "    author_skill: skills/x/SKILL.md\n"
    )
    errs = validate(good_project)
    assert any("name" in e.path for e in errs)


def test_validate_reports_unknown_predecessor(good_project: Path) -> None:
    stages_path = good_project / "config" / "stages.yaml"
    stages_path.write_text(
        "stages:\n"
        "  - name: alpha\n"
        "    author_skill: skills/a/SKILL.md\n"
        "  - name: beta\n"
        "    author_skill: skills/b/SKILL.md\n"
        "    requires_predecessor: nonexistent\n"
    )
    errs = validate(good_project)
    assert any("unknown predecessor" in e.message for e in errs)


def test_validate_reports_breakpoint_referring_to_unknown_stage(good_project: Path) -> None:
    dp_path = good_project / "config" / "dualpass.json"
    data = json.loads(dp_path.read_text())
    data["breakpoints"]["does-not-exist"] = True
    dp_path.write_text(json.dumps(data))
    errs = validate(good_project)
    assert any("does-not-exist" in e.path for e in errs)


def test_validate_reports_yaml_parse_error(good_project: Path) -> None:
    (good_project / "config" / "stages.yaml").write_text("stages: [unbalanced bracket\n")
    errs = validate(good_project)
    assert any("parse error" in e.message for e in errs)


# ── Strict loaders raise ConfigError ─────────────────────────────────────────


def test_load_project_config_raises_on_invalid_data(good_project: Path) -> None:
    dp_path = good_project / "config" / "dualpass.json"
    data = json.loads(dp_path.read_text())
    del data["schema_version"]
    dp_path.write_text(json.dumps(data))
    with pytest.raises(ConfigError) as exc:
        load_project_config(good_project)
    assert any("schema_version" in e.message for e in exc.value.errors)
