"""`dualpass init` implementation.

Copies a bundled example project into a target directory and rewrites the
project name in `config/dualpass.json` to match the target. Idempotent enough
to be re-run on a target that hasn't been initialized yet, but refuses to
overwrite a target that already contains files (other than `.git/` or
hidden cruft like `.DS_Store`).

Template discovery is dual-path: in editable installs the canonical example
at `<repo>/examples/coding-agent/` is used directly; in wheel installs the
hatchling `force-include` copy at `dualpass/_templates/coding-agent/` is used.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

# Files that are ignored when deciding whether a target directory is "empty
# enough" to scaffold into. Lets `dualpass init .` work inside a fresh
# `git init`'d directory.
_IGNORED_TARGET_ENTRIES = frozenset({".git", ".DS_Store", ".gitkeep"})


@dataclass(frozen=True)
class InitResult:
    """Outcome of a successful init call."""

    target: Path
    template_name: str
    project_name: str
    files_copied: int


class InitError(Exception):
    """Raised when init cannot proceed (target exists+populated, template missing, etc.)."""


def _template_root(template_name: str) -> Path:
    """Locate the bundled template tree.

    Search order:
      1. `<package_dir>/_templates/<template_name>/`   (wheel install — hatchling
         force-include lands the example here at build time)
      2. `<repo_root>/examples/<template_name>/`        (editable install — used
         during dev and CI)
    """
    bundled = Path(__file__).resolve().parent / "_templates" / template_name
    if bundled.is_dir():
        return bundled

    repo_root = Path(__file__).resolve().parent.parent.parent
    dev = repo_root / "examples" / template_name
    if dev.is_dir():
        return dev

    raise InitError(
        f"template {template_name!r} not found. Looked at:\n"
        f"  - {bundled}\n"
        f"  - {dev}\n"
        f"This is a packaging bug — please file an issue."
    )


def _target_is_scaffoldable(target: Path) -> bool:
    """Return True if target is empty (ignoring `.git/`, `.DS_Store`, `.gitkeep`)."""
    if not target.exists():
        return True
    if not target.is_dir():
        return False
    return all(entry.name in _IGNORED_TARGET_ENTRIES for entry in target.iterdir())


def _derive_project_name(target: Path) -> str:
    """Default `project_name` is the target directory's basename."""
    return target.resolve().name


def _rewrite_project_name(dualpass_json: Path, project_name: str) -> None:
    data = json.loads(dualpass_json.read_text(encoding="utf-8"))
    data["project_name"] = project_name
    dualpass_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _copy_tree(src: Path, dst: Path) -> int:
    """Copy src/ into dst/ recursively, skipping cruft. Returns file count copied."""
    count = 0
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.rglob("*"):
        if entry.is_dir():
            continue
        # Skip noise the user definitely doesn't want
        if entry.name in (".DS_Store", "__pycache__"):
            continue
        if "/__pycache__/" in str(entry):
            continue
        rel = entry.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, out)
        count += 1
    return count


def run_init(
    target: Path, *, template: str = "coding-agent", project_name: str | None = None
) -> InitResult:
    """Scaffold a new dualpass project. Raises InitError on any precondition failure.

    Args:
      target: Directory to scaffold into. Will be created if it doesn't exist.
      template: Bundled template name (currently only `coding-agent`).
      project_name: Override for the project name written into
        `config/dualpass.json`. Defaults to the target directory's basename.
    """
    target = target.resolve()

    if not _target_is_scaffoldable(target):
        raise InitError(
            f"target directory {target} is not empty (refusing to overwrite). "
            f"Pick a different path, or empty this one first."
        )

    template_root = _template_root(template)
    files_copied = _copy_tree(template_root, target)

    # Rewrite project_name in the copied dualpass.json so the new project has a
    # sensible default identity instead of "coding-agent (example)".
    derived_name = project_name or _derive_project_name(target)
    dp_json = target / "config" / "dualpass.json"
    if dp_json.is_file():
        _rewrite_project_name(dp_json, derived_name)

    return InitResult(
        target=target,
        template_name=template,
        project_name=derived_name,
        files_copied=files_copied,
    )


def format_next_steps(result: InitResult) -> str:
    """Produce the user-facing 'what to do next' block printed after init."""
    return (
        f"Scaffolded {result.template_name!r} into {result.target}\n"
        f"  files written: {result.files_copied}\n"
        f"  project name: {result.project_name}\n\n"
        f"Next steps:\n"
        f"  1. cd {result.target}\n"
        f"  2. dualpass doctor             # confirm agent CLIs + config are healthy\n"
        f"  3. Edit config/agents.yaml to point at the CLIs you actually have installed\n"
        f"  4. Edit skills/<stage>/SKILL.md for each stage to teach the agent your domain\n"
        f"  5. dualpass run --unit demo-001 --provider mock   # (lands in v0.2.0)\n"
    )
