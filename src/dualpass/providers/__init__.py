"""Author and reviewer providers.

A Provider abstracts over the agent CLI(s) that actually do the work. For now
the only shipping implementation is `MockProvider`, which is deterministic,
offline, and produces real artifacts on disk — enough to exercise the
controller end-to-end without spending tokens.

The live provider (subprocess-based, talks to `claude` and `cursor-agent`)
lands in a later milestone.
"""

from __future__ import annotations

from dualpass.config import AgentsConfig

from .base import (
    AuthorResult,
    Provider,
    ReviewResult,
    ReviewVerdict,
    StageContext,
)
from .live import LiveProvider, LiveProviderError
from .mock import MockMarker, MockProvider, MockScript


def get_provider(name: str, *, agents_config: AgentsConfig | None = None) -> Provider:
    """Resolve a provider by name.

    The `live` provider needs `agents_config` to know which CLIs to spawn;
    `mock` ignores it.
    """
    if name == "mock":
        return MockProvider()
    if name == "live":
        if agents_config is None:
            raise ValueError(
                "the 'live' provider requires agents_config — load it from "
                "config/agents.yaml before calling get_provider"
            )
        return LiveProvider(agents_config)
    raise ValueError(f"unknown provider: {name!r}")


__all__ = [
    "AuthorResult",
    "LiveProvider",
    "LiveProviderError",
    "MockMarker",
    "MockProvider",
    "MockScript",
    "Provider",
    "ReviewResult",
    "ReviewVerdict",
    "StageContext",
    "get_provider",
]
