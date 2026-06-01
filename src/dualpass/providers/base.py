"""Provider ABC + shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dualpass.config import StageConfig

ReviewVerdict = Literal["approved", "rejected", "blocked"]


@dataclass(frozen=True)
class StageContext:
    """Everything a provider needs to invoke an author or reviewer for one stage round."""

    unit_id: str
    stage: StageConfig
    round_number: int  # 1-indexed
    units_dir: Path  # .dualpass-state/<unit>/
    project_root: Path  # where config/ lives
    # Used by the dual-pass parallel reviewer to disambiguate two concurrent
    # reviewer invocations writing artifacts to the same units_dir. When set,
    # providers must append "-{pass_label}" to the review filename so the two
    # parallel writes don't clobber each other.
    pass_label: str | None = None


@dataclass(frozen=True)
class AuthorResult:
    """What an author invocation returned."""

    artifact_path: Path
    served_by: str  # which provider produced this — useful for diagnostics
    extras: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewResult:
    """What a reviewer invocation returned."""

    verdict: ReviewVerdict
    review_artifact: Path
    served_by: str
    findings: list[dict[str, object]] | None = None


class Provider(ABC):
    """Interface every author/reviewer provider implements."""

    @abstractmethod
    def invoke_author(self, ctx: StageContext) -> AuthorResult:
        """Produce the stage artifact. Must write at least one file under ctx.units_dir."""

    @abstractmethod
    def invoke_reviewer(self, ctx: StageContext, artifact: AuthorResult) -> ReviewResult:
        """Review the stage artifact. Must write a review note under ctx.units_dir."""
