"""Built-in gate implementations registered with the harness on import.

Each gate is a small, focused check intended to catch a specific failure mode
cheaply, before a reviewer subprocess is spent on it. Gates are deliberately
forgiving: they read files, accept missing optional config, and turn unexpected
exceptions into a failed :class:`GateResult` rather than crashing the loop.

The five gates registered here are referenced by name from ``stages.yaml``:

* ``check-frontmatter``
* ``check-line-citations``
* ``check-single-flight``
* ``check-marker-frontmatter``
* ``check-acceptance-criteria-wording``
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from dualpass.gates import GateContext, GateResult, register_gate
from dualpass.memory import lock_path, read_build_marker

# ── Helpers ────────────────────────────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\s*\n?", re.DOTALL)
_LEADING_NOISE_RE = re.compile(r"\A(?:<!--.*?-->\s*\n)+", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return ``(frontmatter_dict_or_None, body_text_without_frontmatter)``.

    The frontmatter must be delimited by ``---`` fences and parse as a YAML
    mapping. Leading HTML comments are tolerated — providers may prepend
    diagnostic comment lines (e.g. ``<!-- served-by: ... -->``) above the
    frontmatter, and stripping those before matching keeps the gate honest
    against real artifacts. Anything else yields ``(None, original_text)``.
    """
    noise = _LEADING_NOISE_RE.match(text)
    offset = noise.end() if noise else 0
    match = _FRONTMATTER_RE.match(text[offset:])
    if match is None:
        return None, text
    try:
        parsed = yaml.safe_load(match.group("body"))
    except yaml.YAMLError:
        return None, text
    if not isinstance(parsed, dict):
        return None, text
    return parsed, text[offset + match.end():]


# ── 1. check-frontmatter ───────────────────────────────────────────────────────


def check_frontmatter(ctx: GateContext) -> GateResult:
    """Confirm the artifact starts with YAML frontmatter and required fields.

    Most stage authors lean on frontmatter to declare title, version, and unit
    linkage. Catching a missing or malformed block here saves a reviewer round
    spent rejecting the artifact on form rather than substance. Required field
    names default to ``["title"]`` and can be overridden per stage via the
    gate's ``required_fields`` config entry.
    """
    if not ctx.artifact_path.is_file():
        return GateResult(
            passed=False,
            diagnostic=f"artifact not found: {ctx.artifact_path}",
        )
    text = ctx.artifact_path.read_text(encoding="utf-8")
    frontmatter, _ = _split_frontmatter(text)
    if frontmatter is None:
        return GateResult(
            passed=False,
            diagnostic="artifact is missing a YAML frontmatter block delimited by '---' fences",
        )
    required = ["title"]
    if ctx.config and isinstance(ctx.config.get("required_fields"), list):
        required = list(ctx.config["required_fields"])
    missing = [field for field in required if field not in frontmatter]
    if missing:
        return GateResult(
            passed=False,
            diagnostic=f"frontmatter missing required field(s): {', '.join(missing)}",
        )
    return GateResult(passed=True, diagnostic="frontmatter present with required fields")


register_gate("check-frontmatter", check_frontmatter)


# ── 2. check-line-citations ────────────────────────────────────────────────────


_CITATION_RE = re.compile(r"(?P<file>[\w./\-]+\.\w+):(?P<line>\d+)")


def check_line_citations(ctx: GateContext) -> GateResult:
    """Resolve every ``file:line`` reference in the artifact body.

    Stale citations are a common failure: the agent quotes a line that no
    longer exists or points at a file that was renamed. A reviewer can be
    coached to verify them, but a deterministic gate catches the cheap class
    in one pass. When ``verify_lines`` is set in the gate config, the gate
    also checks that each cited line number is in range for the target file.
    """
    if not ctx.artifact_path.is_file():
        return GateResult(
            passed=False,
            diagnostic=f"artifact not found: {ctx.artifact_path}",
        )
    text = ctx.artifact_path.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    matches = list(_CITATION_RE.finditer(body))
    if not matches:
        return GateResult(passed=True, diagnostic="no file:line citations to verify")

    verify_lines = bool(ctx.config and ctx.config.get("verify_lines"))
    unresolved: list[tuple[str, int, str]] = []
    for match in matches:
        rel = match.group("file")
        line_no = int(match.group("line"))
        target = (ctx.project_root / rel).resolve()
        if not target.exists():
            unresolved.append((rel, line_no, "file does not exist"))
            continue
        if verify_lines and target.is_file():
            try:
                line_count = sum(1 for _ in target.open("r", encoding="utf-8", errors="replace"))
            except OSError as exc:
                unresolved.append((rel, line_no, f"cannot read file: {exc}"))
                continue
            if line_no < 1 or line_no > line_count:
                unresolved.append(
                    (rel, line_no, f"line out of range (file has {line_count} lines)")
                )

    if not unresolved:
        return GateResult(
            passed=True,
            diagnostic=f"resolved {len(matches)} file:line citation(s)",
        )

    capped = unresolved[:10]
    lines = [f"  - {rel}:{line_no} — {reason}" for rel, line_no, reason in capped]
    suffix = "" if len(unresolved) <= 10 else f"\n  ... and {len(unresolved) - 10} more"
    citations = [(rel, line_no) for rel, line_no, _ in capped]
    return GateResult(
        passed=False,
        diagnostic="unresolved file:line citations:\n" + "\n".join(lines) + suffix,
        citations=citations,
    )


register_gate("check-line-citations", check_line_citations)


# ── 3. check-single-flight ─────────────────────────────────────────────────────


def check_single_flight(ctx: GateContext) -> GateResult:
    """Refuse to proceed when another process holds the unit's pipeline lock.

    Two orchestrators racing on the same unit will trash each other's
    artifacts and event logs. The single-flight lockfile names the holding
    pid; this gate passes when the lock is absent or the current process owns
    it, and fails otherwise with the foreign pid in the diagnostic.
    """
    path = lock_path(ctx.unit_id, ctx.project_root)
    if not path.is_file():
        return GateResult(passed=True, diagnostic="no pipeline lock held")
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return GateResult(
            passed=False,
            diagnostic=f"pipeline lockfile present but unreadable: {exc}",
        )
    holder_pid = payload.get("pid")
    current_pid = os.getpid()
    if holder_pid == current_pid:
        return GateResult(
            passed=True,
            diagnostic=f"pipeline lock held by this process (pid={current_pid})",
        )
    return GateResult(
        passed=False,
        diagnostic=(
            f"pipeline lock held by another process (pid={holder_pid}); "
            f"refusing to run concurrently"
        ),
    )


register_gate("check-single-flight", check_single_flight)


# ── 4. check-marker-frontmatter ────────────────────────────────────────────────


def check_marker_frontmatter(ctx: GateContext) -> GateResult:
    """Validate the build-complete marker for the unit's current stage.

    The build-complete marker tells the controller what the author intends
    next (continue, stop, escalate) and why. A malformed or missing marker
    leaves the loop without a signal, so we surface the parse error directly
    rather than letting the controller fail later.
    """
    marker_path = ctx.project_root / ".dualpass-state" / f"{ctx.unit_id}-build-complete.md"
    if not marker_path.is_file():
        return GateResult(
            passed=False,
            diagnostic=(
                f"build-complete marker not found at {marker_path}. "
                f"The author stage should emit this file when it finishes."
            ),
        )
    try:
        marker = read_build_marker(ctx.unit_id, ctx.stage, ctx.project_root)
    except Exception as exc:
        return GateResult(
            passed=False,
            diagnostic=f"build-complete marker failed to parse: {exc}",
        )
    if marker is None:
        return GateResult(
            passed=False,
            diagnostic="build-complete marker parser returned no result",
        )
    return GateResult(passed=True, diagnostic="build-complete marker parsed successfully")


register_gate("check-marker-frontmatter", check_marker_frontmatter)


# ── 5. check-acceptance-criteria-wording ───────────────────────────────────────


_AC_HEADER_RE = re.compile(
    r"(?im)^\s*(?:#+\s*acceptance\s+criteria\b|acceptance\s+criteria\s*:|ac\d+\s*:)"
)
_BRITTLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bexactly\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bmust\s+total\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bshall\s+total\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bshall\s+be\s+\d+\b", re.IGNORECASE),
    re.compile(r"=\s*\d+\s+tests?\b", re.IGNORECASE),
)
_AC_CONTEXT_WINDOW = 30  # lines of context above a brittle-pattern line


def check_acceptance_criteria_wording(ctx: GateContext) -> GateResult:
    """Flag exact-count phrasings inside acceptance-criteria sections.

    Brittle exact-count phrasing ("must add exactly 12 tests") creates a
    treadmill: every incidental edit forces the agent to renumber the
    criterion in lockstep with the code, and reviewers spend rounds chasing
    drift between the two. Looser wording ("add at least 12 tests covering …")
    captures the same intent without the coupling. This gate scans for the
    brittle patterns only inside an acceptance-criteria context so unrelated
    prose with bare numbers is not penalised.
    """
    if not ctx.artifact_path.is_file():
        return GateResult(
            passed=False,
            diagnostic=f"artifact not found: {ctx.artifact_path}",
        )
    text = ctx.artifact_path.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    lines = body.splitlines()

    flagged: list[tuple[str, int]] = []
    diagnostics: list[str] = []
    for idx, line in enumerate(lines):
        if not any(pat.search(line) for pat in _BRITTLE_PATTERNS):
            continue
        window_start = max(0, idx - _AC_CONTEXT_WINDOW)
        window = "\n".join(lines[window_start : idx + 1])
        if _AC_HEADER_RE.search(window) is None:
            continue
        line_no = idx + 1
        flagged.append((str(ctx.artifact_path), line_no))
        diagnostics.append(f"  - line {line_no}: {line.strip()}")

    if not flagged:
        return GateResult(
            passed=True,
            diagnostic="no brittle exact-count phrasings detected in acceptance criteria",
        )
    return GateResult(
        passed=False,
        diagnostic=(
            "brittle exact-count phrasing inside acceptance criteria "
            "(prefer 'at least N' or similar):\n" + "\n".join(diagnostics)
        ),
        citations=flagged,
    )


register_gate("check-acceptance-criteria-wording", check_acceptance_criteria_wording)
