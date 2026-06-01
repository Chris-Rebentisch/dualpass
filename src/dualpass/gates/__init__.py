"""Preflight gate plugins (extension point — no built-ins ship in v1).

A gate is a callable returning `(passed: bool, detail: str)`. Gates run
before a stage author is invoked; a failed gate halts the stage round with
the detail string as the diagnostic.

**v1.0.0 status:** no built-in gates ship with the harness. The
configuration surface is in `config/stages.yaml` (`preflight_gates: [...]`
per stage) — referenced gate names that aren't registered raise a clear
error rather than silently passing. Projects bring their own gates by
registering them via `register_gate(name, callable)`.

The historical built-in list (frontmatter validation, line-citation
verification, single-flight check, marker-frontmatter parser, AC1-wording
lint) is preserved in stage skills as instructions to the author/reviewer
rather than as harness-level gates — that pattern composes better across
projects with different stack assumptions.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["register_gate", "get_gate", "registered_gate_names"]

GateFn = Callable[..., "tuple[bool, str]"]

_REGISTRY: dict[str, GateFn] = {}


def register_gate(name: str, fn: GateFn) -> None:
    """Register a gate under `name`. Replaces an existing registration if any."""
    _REGISTRY[name] = fn


def get_gate(name: str) -> GateFn | None:
    """Look up a registered gate by name. Returns None if not registered."""
    return _REGISTRY.get(name)


def registered_gate_names() -> list[str]:
    """Return the sorted list of currently-registered gate names."""
    return sorted(_REGISTRY)
