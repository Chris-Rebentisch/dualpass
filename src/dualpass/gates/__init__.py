"""Preflight gate registry — named, lightweight checks that run before a reviewer.

A gate is a callable receiving a :class:`GateContext` and returning a
:class:`GateResult`. Stages declare which gates to run via
``preflight_gates: [...]`` in ``stages.yaml``. The controller invokes each gate
in declared order; gates do **not** short-circuit on failure so the agent sees
the full diagnostic surface in one pass.

Five built-ins are registered at import time from :mod:`dualpass.gates.builtins`:

* ``check-frontmatter`` — YAML frontmatter presence and required-field check.
* ``check-line-citations`` — resolves ``file:line`` references in the artifact.
* ``check-single-flight`` — refuses to run when another process holds the lock.
* ``check-marker-frontmatter`` — validates the build-complete marker.
* ``check-acceptance-criteria-wording`` — flags brittle exact-count phrasings.

Projects register their own gates with :func:`register_gate`; later
registrations replace earlier ones under the same name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "GateContext",
    "GateResult",
    "Gate",
    "register_gate",
    "get_gate",
    "list_gates",
    "run_gates",
]


@dataclass(frozen=True)
class GateContext:
    """Inputs handed to a gate when it runs.

    ``config`` is the optional per-gate configuration block from
    ``stages.yaml``; gates that take no configuration ignore it.
    """

    unit_id: str
    stage: str
    project_root: Path
    artifact_path: Path
    config: dict | None = None


@dataclass(frozen=True)
class GateResult:
    """Outcome of one gate invocation.

    ``citations`` is an optional list of ``(file_path, line_number)`` tuples
    that a gate can use to point the agent at the exact lines it flagged.
    """

    passed: bool
    diagnostic: str
    citations: list[tuple[str, int]] | None = None


class Gate(Protocol):
    def __call__(self, ctx: GateContext) -> GateResult: ...


_REGISTRY: dict[str, Gate] = {}


def register_gate(name: str, fn: Gate) -> None:
    """Register a gate under ``name``. Replaces any prior registration."""
    _REGISTRY[name] = fn


def get_gate(name: str) -> Gate | None:
    """Look up a registered gate by name. Returns ``None`` if not registered."""
    return _REGISTRY.get(name)


def list_gates() -> list[str]:
    """Return the sorted list of currently-registered gate names."""
    return sorted(_REGISTRY)


def run_gates(names: list[str], ctx: GateContext) -> list[GateResult]:
    """Run gates in declared order and collect one :class:`GateResult` per name.

    An unregistered gate name produces a synthetic failure result naming the
    available gates rather than raising — this keeps the controller robust
    against config typos and lets the agent see the full diagnostic surface
    in a single round.

    Gates always run to completion; this function never short-circuits on
    failure.
    """
    results: list[GateResult] = []
    for name in names:
        gate = _REGISTRY.get(name)
        if gate is None:
            available = ", ".join(sorted(_REGISTRY)) or "(none registered)"
            results.append(
                GateResult(
                    passed=False,
                    diagnostic=(
                        f"gate {name!r} is not registered. Available: {available}"
                    ),
                )
            )
            continue
        try:
            results.append(gate(ctx))
        except Exception as exc:  # defensive: a buggy gate must not crash the loop
            results.append(
                GateResult(
                    passed=False,
                    diagnostic=f"gate {name!r} raised {type(exc).__name__}: {exc}",
                )
            )
    return results


# Register the built-in gates as a side effect of importing the gates package.
# This keeps the API ergonomic — `from dualpass import gates` is enough to use
# the named built-ins. The import is at module bottom to avoid a circular
# reference (builtins imports GateContext / GateResult from this module).
from dualpass.gates import builtins as _builtins  # noqa: E402,F401
