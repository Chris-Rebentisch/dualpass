"""Reviewer types — thin re-export of the provider-layer reviewer interface.

The cross-vendor reviewer fallback mechanic — dualpass's headline feature —
lives in `dualpass.providers.LiveProvider.invoke_reviewer`. This module
re-exports `ReviewResult` and `ReviewVerdict` under their historical names so
older imports continue to resolve.

`review(...)` is intentionally not wired here. The canonical path is:

    from dualpass.providers import get_provider
    provider = get_provider("live", agents_config=cfg.agents)
    result = provider.invoke_reviewer(ctx, author_result)

That path handles transient retries, cross-vendor fallback on
`[resource_exhausted]`, and the dual-pass parallel reviewer pattern. Calling
`reviewer.review(...)` directly raises NotImplementedError pointing at the
real path.
"""

from __future__ import annotations

from pathlib import Path

from dualpass.providers.base import ReviewResult, ReviewVerdict

__all__ = ["ReviewResult", "ReviewVerdict", "review"]


def review(
    artifact: Path,
    *,
    stage: str,
    unit_id: str,
    reviewer_skill: Path,
    project_root: Path,
    dual_pass: bool = False,
) -> ReviewResult:
    """Historical entry point — not wired.

    Use `providers.get_provider("live", agents_config=...).invoke_reviewer(...)` instead.
    That path implements the same contract plus transient retry + cross-vendor
    fallback + the dual-pass parallel reviewer pattern.
    """
    raise NotImplementedError(
        "reviewer.review is not yet wired — use providers.LiveProvider.invoke_reviewer "
        "instead. Cross-vendor fallback + dual-pass parallel review live there."
    )
