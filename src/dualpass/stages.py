"""Stage abstraction — thin re-export of the config-layer stage types.

`StageConfig` (the canonical stage type) lives in `dualpass.config`. This
module re-exports it under the historical name `StageDef` for code that
imports from `dualpass.stages`. New code should import directly from
`dualpass.config`.

`load_stages(...)` is no longer wired here either — `config.load_stages`
is the actual loader and is what `controller.run_unit` uses. The function
here exists only so `from dualpass.stages import load_stages` still
resolves; it delegates to the config-layer implementation.
"""

from __future__ import annotations

from pathlib import Path

from dualpass.config import StageConfig
from dualpass.config import load_stages as _config_load_stages

# Historical name — kept so old code keeps working. Prefer
# `dualpass.config.StageConfig` in new code.
StageDef = StageConfig


def load_stages(project_root: Path):
    """Delegate to `dualpass.config.load_stages`. Kept here for import-path stability."""
    return _config_load_stages(project_root)
