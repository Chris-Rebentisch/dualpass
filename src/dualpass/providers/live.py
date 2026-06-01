"""Subprocess-based provider — real `claude` / `cursor-agent` invocations.

The headline feature of dualpass: cross-vendor independent review with
transparent fallback. The author shells out to the `author` role's CLI; the
reviewer shells out to the `reviewer` role's CLI. When the primary reviewer
returns an output matching the configured `exhaustion_patterns`
(typically `[resource_exhausted]`) for `activate_after_consecutive_exhausted`
consecutive calls, the harness swaps to `reviewer_fallback` — usually a
different vendor — so review never silently drops.

Subprocess discipline:
  - Command template uses `{prompt}` placeholder. We shlex-split the
    template (no shell interpretation) and substitute the placeholder with
    the prompt as a single argv element. This is injection-safe.
  - `timeout_seconds` from the role bounds each subprocess. Timeouts are
    fatal to that round and surface as a "blocked" verdict.
  - `transient_retry_patterns` (e.g. `ETIMEDOUT`, `[unavailable]`) trigger
    bounded retry with `transient_retry_delay_seconds` between attempts.
  - `exhaustion_patterns` increment a per-role consecutive-exhaustion counter.
    Any non-matching response zeros the counter.

Prompt building:
  - Stage skill file (e.g. `skills/spec/SKILL.md`) is read and wrapped in
    a `<skill>` block. If the file is missing we proceed with an empty
    skill — the LLM will produce something, just probably worse.
  - Author prompt asks for a markdown document written directly to stdout.
  - Reviewer prompt embeds the artifact and asks for a final `Verdict: ...`
    line. We parse the first valid verdict line in the response.

This module is deliberately framework-free: stdlib only. No vendor SDKs,
no httpx, no JSON-envelope assumptions. It speaks "argv + stdin → stdout"
because that's what every shipping agent CLI does.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from dualpass.config import AgentRole, AgentsConfig

from .base import AuthorResult, Provider, ReviewResult, ReviewVerdict, StageContext

logger = logging.getLogger(__name__)


# ── Public errors ────────────────────────────────────────────────────────────


class LiveProviderError(Exception):
    """Raised when the live provider hits an unrecoverable error."""


# ── Subprocess outcome ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Outcome:
    """Internal: aggregate of one (potentially-retried) subprocess invocation."""

    stdout: str
    stderr: str
    returncode: int
    served_by: str  # which role label answered ("reviewer" or "reviewer_fallback")
    attempts: int  # 1 = no retry, 2 = one retry, etc.


# ── Prompt construction ──────────────────────────────────────────────────────


def _read_skill(project_root: Path, rel_path: str) -> str:
    """Return the contents of a skill file, or '' if missing.

    A missing skill file isn't fatal — the LLM still has the stage name and
    can produce something. We log so operators notice.
    """
    full = project_root / rel_path
    if not full.is_file():
        logger.warning("live provider: skill file missing at %s", full)
        return ""
    return full.read_text(encoding="utf-8")


def build_author_prompt(ctx: StageContext) -> str:
    """Construct the author prompt for one stage round."""
    skill_text = _read_skill(ctx.project_root, str(ctx.stage.author_skill))
    return (
        f'<skill name="{ctx.stage.name}">\n{skill_text}\n</skill>\n\n'
        f"<task>\n"
        f"You are producing the {ctx.stage.name!r} artifact for unit "
        f"{ctx.unit_id!r} (round {ctx.round_number}).\n"
        f"Write your output directly to stdout as a complete markdown document. "
        f"Do not add commentary outside the document body.\n"
        f"</task>\n"
    )


def build_reviewer_prompt(ctx: StageContext, artifact: AuthorResult) -> str:
    """Construct the reviewer prompt for one stage round."""
    skill_text = (
        _read_skill(ctx.project_root, str(ctx.stage.reviewer_skill))
        if ctx.stage.reviewer_skill
        else ""
    )
    artifact_text = artifact.artifact_path.read_text(encoding="utf-8")
    return (
        f'<skill name="{ctx.stage.name}-reviewer">\n{skill_text}\n</skill>\n\n'
        f'<artifact stage="{ctx.stage.name}" unit="{ctx.unit_id}" round="{ctx.round_number}">\n'
        f"{artifact_text}\n"
        f"</artifact>\n\n"
        f"<task>\n"
        f"Review the artifact above. Write your review as markdown.\n"
        f"End your response with EXACTLY ONE LINE of the form:\n"
        f"  Verdict: approved\n"
        f"  Verdict: rejected\n"
        f"  Verdict: blocked\n"
        f"</task>\n"
    )


# ── Output parsing ───────────────────────────────────────────────────────────


_VALID_VERDICTS: tuple[ReviewVerdict, ...] = ("approved", "rejected", "blocked")


def parse_verdict(text: str) -> ReviewVerdict:
    """Find the verdict in a reviewer response.

    Walks the response from the end backwards (so a verdict mentioned in
    passing earlier won't shadow the final one). Returns 'blocked' if nothing
    is recognizable — that's the conservative default and forces the operator
    to look at the review.
    """
    for line in reversed(text.splitlines()):
        stripped = line.strip().lower()
        if stripped.startswith("verdict:"):
            value = stripped.split(":", 1)[1].strip()
            for verdict in _VALID_VERDICTS:
                if value.startswith(verdict):
                    return verdict
    return "blocked"


# ── Pattern matching ─────────────────────────────────────────────────────────


def _matches_any(haystack: str, patterns: tuple[str, ...]) -> bool:
    """Substring match. Patterns are plain strings, not regex (keeps config simple)."""
    return any(p and p in haystack for p in patterns)


# ── Subprocess runner ────────────────────────────────────────────────────────


def _format_argv(template: str, prompt: str) -> list[str]:
    """Turn a command template + prompt into a safe argv list."""
    pieces = shlex.split(template)
    return [(prompt if p == "{prompt}" else p) for p in pieces]


def _run_subprocess(argv: list[str], *, timeout: int | None) -> tuple[str, str, int]:
    """Execute argv. Returns (stdout, stderr, returncode)."""
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return (
            exc.stdout.decode("utf-8", "replace") if exc.stdout else "",
            f"[dualpass-timeout] subprocess exceeded {timeout}s",
            124,
        )
    except FileNotFoundError as exc:
        return ("", f"[dualpass-cli-missing] {exc}", 127)
    return (completed.stdout, completed.stderr, completed.returncode)


# ── Live provider ────────────────────────────────────────────────────────────


class LiveProvider(Provider):
    """Real subprocess-driven provider with cross-vendor fallback for reviewer."""

    def __init__(self, agents_config: AgentsConfig) -> None:
        self._agents = agents_config
        # Consecutive-exhaustion counter per reviewer role label.
        self._reviewer_exhaustion_streak: int = 0
        if "author" not in agents_config.roles:
            raise LiveProviderError("agents.yaml has no 'author' role configured")
        if "reviewer" not in agents_config.roles:
            raise LiveProviderError("agents.yaml has no 'reviewer' role configured")

    # ── Author ─────────────────────────────────────────────────────────────

    def invoke_author(self, ctx: StageContext) -> AuthorResult:
        role = self._agents.roles["author"]
        prompt = build_author_prompt(ctx)
        outcome = self._run_with_transient_retry(role, prompt, role_label="author")
        artifact = ctx.units_dir / f"{ctx.stage.name}-artifact-v{ctx.round_number}.md"
        artifact.write_text(_with_diagnostic_header(outcome), encoding="utf-8")
        return AuthorResult(
            artifact_path=artifact,
            served_by=outcome.served_by,
            extras={"attempts": outcome.attempts, "returncode": outcome.returncode},
        )

    # ── Reviewer (with fallback) ───────────────────────────────────────────

    def invoke_reviewer(self, ctx: StageContext, artifact: AuthorResult) -> ReviewResult:
        primary = self._agents.roles["reviewer"]
        fallback = self._agents.roles.get("reviewer_fallback")
        active_role, role_label = self._choose_reviewer(primary, fallback)
        prompt = build_reviewer_prompt(ctx, artifact)
        outcome = self._run_with_transient_retry(active_role, prompt, role_label=role_label)

        # Update exhaustion streak ONLY for the primary reviewer. If we're
        # already on the fallback, we don't escalate further.
        if role_label == "reviewer":
            if _matches_any(outcome.stdout + outcome.stderr, active_role.exhaustion_patterns):
                self._reviewer_exhaustion_streak += 1
                logger.info(
                    "live provider: reviewer exhausted (streak=%d)",
                    self._reviewer_exhaustion_streak,
                )
            else:
                self._reviewer_exhaustion_streak = 0

        verdict = parse_verdict(outcome.stdout)
        suffix = f"-{ctx.pass_label}" if ctx.pass_label else ""
        review = ctx.units_dir / f"{ctx.stage.name}-review-v{ctx.round_number}{suffix}.md"
        review.write_text(_with_diagnostic_header(outcome), encoding="utf-8")
        return ReviewResult(
            verdict=verdict,
            review_artifact=review,
            served_by=outcome.served_by,
            findings=None,
        )

    # ── Internals ──────────────────────────────────────────────────────────

    def _choose_reviewer(
        self, primary: AgentRole, fallback: AgentRole | None
    ) -> tuple[AgentRole, str]:
        """Decide whether to use primary or fallback this turn.

        Fallback activates once the primary has returned exhaustion-matching
        responses for `fallback.activate_after_consecutive_exhausted` consecutive
        calls (default 3 if not configured).
        """
        if fallback is None:
            return primary, "reviewer"
        threshold = fallback.activate_after_consecutive_exhausted or 3
        if self._reviewer_exhaustion_streak >= threshold:
            logger.info(
                "live provider: activating reviewer_fallback (streak=%d ≥ threshold=%d)",
                self._reviewer_exhaustion_streak,
                threshold,
            )
            return fallback, "reviewer_fallback"
        return primary, "reviewer"

    def _run_with_transient_retry(
        self, role: AgentRole, prompt: str, *, role_label: str
    ) -> _Outcome:
        """Spawn the role's CLI, retrying on transient-pattern matches."""
        argv = _format_argv(role.command, prompt)
        max_attempts = (role.transient_retries or 0) + 1
        delay = role.transient_retry_delay_seconds or 0
        last: tuple[str, str, int] = ("", "", -1)
        for attempt in range(1, max_attempts + 1):
            stdout, stderr, returncode = _run_subprocess(argv, timeout=role.timeout_seconds)
            last = (stdout, stderr, returncode)
            combined = stdout + stderr
            if returncode == 0 or not _matches_any(combined, role.transient_retry_patterns):
                return _Outcome(
                    stdout=stdout,
                    stderr=stderr,
                    returncode=returncode,
                    served_by=role_label,
                    attempts=attempt,
                )
            # Transient pattern matched and we have retries left.
            if attempt < max_attempts:
                logger.info(
                    "live provider: %s transient failure (attempt %d/%d) — retrying in %ds",
                    role_label,
                    attempt,
                    max_attempts,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)
        # All attempts exhausted.
        stdout, stderr, returncode = last
        return _Outcome(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            served_by=role_label,
            attempts=max_attempts,
        )


# ── Artifact framing ─────────────────────────────────────────────────────────


def _with_diagnostic_header(outcome: _Outcome) -> str:
    """Prepend a small diagnostic header so the artifact file is auditable."""
    return (
        f"<!-- dualpass-served-by: {outcome.served_by} -->\n"
        f"<!-- dualpass-attempts: {outcome.attempts} -->\n"
        f"<!-- dualpass-returncode: {outcome.returncode} -->\n"
        f"{outcome.stdout}"
    )
