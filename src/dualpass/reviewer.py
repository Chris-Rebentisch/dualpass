"""Dual-pass reviewer with cross-vendor fallback.

The signature feature of dualpass. When the primary reviewer CLI fails with
`[resource_exhausted]` (or any configured exhaustion pattern) N consecutive times,
this module transparently falls back to the configured fallback reviewer. The dual-pass
review contract is preserved even when one vendor is sick.

v0.1.0a0 status: stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ReviewVerdict = Literal["approved", "rejected", "blocked"]


@dataclass
class ReviewResult:
    """Outcome of one reviewer invocation."""

    verdict: ReviewVerdict
    review_artifact: Path
    served_by: str  # which role served the review (e.g. "reviewer" or "reviewer_fallback")
    findings: list[dict] | None = None


def review(
    artifact: Path,
    *,
    stage: str,
    unit_id: str,
    reviewer_skill: Path,
    project_root: Path,
    dual_pass: bool = False,
) -> ReviewResult:
    """Invoke the reviewer (and fallback if exhausted) and return the verdict.

    v0.2.0a0 status: the live (subprocess-based) reviewer is not yet wired. Use
    `providers.MockProvider.invoke_reviewer` for offline runs. The cross-vendor
    fallback mechanic lands once the live provider exists.
    """
    raise NotImplementedError(
        "reviewer.review is not yet wired — v0.2.0a0 ships the mock provider only. "
        "Use providers.MockProvider for offline runs. Live reviewer lands later."
    )
