"""Author and reviewer providers.

A Provider abstracts over the agent CLI(s) that actually do the work. For now
the only shipping implementation is `MockProvider`, which is deterministic,
offline, and produces real artifacts on disk — enough to exercise the
controller end-to-end without spending tokens.

The live provider (subprocess-based, talks to `claude` and `cursor-agent`)
lands in a later milestone.
"""

from __future__ import annotations

from .base import (
    AuthorResult,
    Provider,
    ReviewResult,
    ReviewVerdict,
    StageContext,
)
from .mock import MockProvider, MockScript


def get_provider(name: str) -> Provider:
    """Resolve a provider by name. Raises NotImplementedError for unknown names."""
    if name == "mock":
        return MockProvider()
    if name == "live":
        raise NotImplementedError(
            "the live provider (real subprocess agent invocations) is not yet implemented — "
            "use --provider mock for now, or watch CHANGELOG.md for the milestone"
        )
    raise ValueError(f"unknown provider: {name!r}")


__all__ = [
    "AuthorResult",
    "MockProvider",
    "MockScript",
    "Provider",
    "ReviewResult",
    "ReviewVerdict",
    "StageContext",
    "get_provider",
]
