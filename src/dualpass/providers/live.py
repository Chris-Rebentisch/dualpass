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
  - Author and reviewer artifacts are resolved with a **two-source policy**
    (v1.0.2, ported from GrACE's `scripts/pipeline/run_pipeline.py`):

      1. *File-on-disk wins.* If the agent's own tools (Write, Bash) created
         the expected artifact file at the path the skill instructed, that
         file is the artifact — we just prepend a diagnostic header inline.
         Mirrors `infer_latest_stage_artifact` in run_pipeline.py:773.
      2. *Stdout is the fallback.* If no file appeared on disk, the agent
         streamed markdown to stdout; we write it. This is the original
         v1.0.0 behavior, preserved for skills that explicitly forbid file
         tools.
      3. *JSON envelope defense.* If the captured stdout begins with `{` and
         parses as a JSON envelope with a string `result` field
         (e.g. `claude --output-format json` or `cursor-agent --output-format
         json`), the wrapped `.result` content is unwrapped before writing.
         Mirrors `parse_json_stdout` + `extract_cli_payload` in
         run_pipeline.py:684-743.
  - Reviewer verdict resolution is similarly two-source: prefer a `Verdict:`
    line found in the on-disk review file body (where cursor-agent reliably
    writes); fall back to parsing stdout. Mirrors `review_body_signals_approved`
    in run_pipeline.py:803.

This module is deliberately framework-free: stdlib only. No vendor SDKs,
no httpx.
"""

from __future__ import annotations

import json
import logging
import re
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
        # v1.0.2: file-on-disk-first (port of GrACE's infer_latest_stage_artifact
        # semantics — run_pipeline.py:773). If claude/cursor used their own Write
        # tools to populate the artifact, that file IS the artifact; we just
        # prepend a diagnostic header. Otherwise we fall back to writing stdout.
        _resolve_artifact_path(artifact, outcome)
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

        suffix = f"-{ctx.pass_label}" if ctx.pass_label else ""
        review = ctx.units_dir / f"{ctx.stage.name}-review-v{ctx.round_number}{suffix}.md"
        # v1.0.2: file-on-disk-first for review artifact (mirrors author path).
        _resolve_artifact_path(review, outcome)
        # v1.0.2: verdict-from-disk-first (port of run_pipeline.py:803's
        # review_body_signals_approved). cursor-agent reliably writes its review
        # to the expected file but its stdout is unreliable; the review file body
        # is the authoritative source for the verdict.
        try:
            review_body = review.read_text(encoding="utf-8")
        except OSError:
            review_body = ""
        verdict = _verdict_from_text(review_body)
        if verdict is None:
            verdict = parse_verdict(outcome.stdout)
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


# v1.0.4: match ONE OR MORE consecutive header triplets (claude sometimes
# inlines a second copy of the header in its own output during revision rounds,
# treating the on-disk diagnostic preamble as part of the artifact structure).
# A single regex match now consumes the entire stack so prepend-fresh leaves
# exactly one header (during the deprecated inline-header path) or zero
# (during the v1.0.4+ sidecar path).
_DIAGNOSTIC_HEADER_RE = re.compile(
    r"^(?:<!-- dualpass-served-by:.*?-->\n"
    r"<!-- dualpass-attempts:.*?-->\n"
    r"<!-- dualpass-returncode:.*?-->\n)+",
    re.DOTALL,
)

# v1.0.4: strip claude's narration preamble between the start of the file
# and the YAML frontmatter opener. Only applied when frontmatter is present —
# stages using H1 + bold-prefix headers (outline/spec/prompt/audit/handoff
# in the bundled v1.0.3 config) legitimately start with prose.
_FRONTMATTER_OPEN_RE = re.compile(r"^---\s*\n", re.MULTILINE)


def _meta_sidecar_path(artifact_path: Path) -> Path:
    """Return the meta-sidecar path for an artifact (v1.0.4).

    `<dir>/<stage>-artifact-v<N>.md` → `<dir>/<stage>-artifact-v<N>.meta.json`
    `<dir>/<stage>-review-v<N>-a.md` → `<dir>/<stage>-review-v<N>-a.meta.json`
    """
    return artifact_path.with_suffix(".meta.json")


def _write_meta_sidecar(artifact_path: Path, outcome: _Outcome) -> None:
    """Write subprocess diagnostics next to the artifact (v1.0.4).

    Replaces v1.0.2's HTML-comment header inside the artifact body. Keeping
    harness metadata out of the file the agent sees prevents the recursive
    reproduction loop where claude (during revision rounds) treats the
    diagnostic header as part of the artifact structure and stuffs additional
    copies into its own output.
    """
    payload = {
        "served_by": outcome.served_by,
        "attempts": outcome.attempts,
        "returncode": outcome.returncode,
    }
    _meta_sidecar_path(artifact_path).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _sanitize_artifact_body(text: str) -> str:
    """Strip harness noise from an agent's artifact output (v1.0.4).

    Three sanitization passes, in order:

    1. **JSON envelope unwrap.** If the entire payload is a Claude/Cursor
       `--output-format json` envelope (object with string `result` field),
       extract `.result`. Mirrors `parse_json_stdout` + `extract_cli_payload`
       in the source pipeline at run_pipeline.py:684-743.
    2. **Diagnostic header strip.** Remove one or more stacked
       `<!-- dualpass-served-by ... -->` triplets at the top. Backward-compat
       with v1.0.2/v1.0.3 artifacts that carry the header inline. v1.0.4+
       artifacts won't have them (sidecar instead) but agent revisions may
       reintroduce them when claude reads + rewrites an existing artifact.
    3. **Preamble strip.** If a `---` frontmatter line exists in the body,
       discard everything before it (after the header strip in step 2). This
       catches the narrate-before-structure pattern that claude reliably
       exhibits across revision rounds even when the gate feedback names it
       directly. Stages without YAML frontmatter (H1+bold-line shape) keep
       their leading prose unchanged.
    """
    # Strip diagnostic headers FIRST so a JSON envelope hidden behind them
    # (the v1.0.1-bug-on-disk case) is reachable by the unwrap that follows.
    text = _DIAGNOSTIC_HEADER_RE.sub("", text, count=1)
    text = _unwrap_json_envelope(text)
    # Strip preamble prose only when the body has YAML frontmatter — for
    # H1+bold-line stages, prose at the top is the artifact.
    fm_match = _FRONTMATTER_OPEN_RE.search(text)
    if fm_match and fm_match.start() > 0:
        text = text[fm_match.start():]
    return text


def _unwrap_json_envelope(text: str) -> str:
    """If `text` is a Claude/Cursor JSON envelope, return its `.result` field.

    Behavior (mirrors run_pipeline.py:684-743):
      - `{"result": "...markdown..."}` → returns the markdown
      - `{"type": "result", "result": "..."}` → same
      - Anything that fails to parse as JSON → returned unchanged
      - JSON object without a string `result` field → returned unchanged

    The unwrap is best-effort and defensive: when in doubt, return the
    original string so we never destroy useful content.
    """
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict):
        return text
    result = parsed.get("result")
    if isinstance(result, str):
        return result
    return text


def _resolve_artifact_path(expected: Path, outcome: _Outcome) -> None:
    """File-on-disk-first artifact resolution (v1.0.2; v1.0.4 sidecar metadata).

    Two-source artifact resolution, in order:

    1. **File on disk.** If the agent's Write tool populated the expected
       artifact path, that file IS the artifact. Mirrors
       `infer_latest_stage_artifact` in the source pipeline (run_pipeline.py:773).
    2. **Stdout fallback.** If no file appeared, the agent streamed markdown
       to stdout. Use that.

    Both sources are sanitized by `_sanitize_artifact_body` (JSON envelope
    unwrap, diagnostic-header strip, preamble strip). v1.0.4 writes the
    artifact body as PURE MARKDOWN — diagnostic info goes to a `.meta.json`
    sidecar via `_write_meta_sidecar`. Keeping harness metadata out of the
    artifact body prevents agents from treating the header as part of the
    artifact structure and reproducing it during revision rounds (the
    failure mode observed in v1.0.3 chunk-77a live testing).

    Backward compatibility: artifacts created by v1.0.0–v1.0.3 carry the
    diagnostic header inline. `_sanitize_artifact_body` strips them so a
    mid-revision upgrade from an earlier version works without loss.
    """
    on_disk = ""
    if expected.exists():
        try:
            on_disk = expected.read_text(encoding="utf-8")
        except OSError:
            on_disk = ""

    source = on_disk if on_disk.strip() else outcome.stdout
    body = _sanitize_artifact_body(source)
    expected.write_text(body, encoding="utf-8")
    _write_meta_sidecar(expected, outcome)


_VERDICT_APPROVED_RE = re.compile(
    r"^\s*Verdict:\s*Approved\s*$", re.IGNORECASE | re.MULTILINE
)
_VERDICT_REJECTED_RE = re.compile(
    r"^\s*Verdict:\s*Rejected\s*$", re.IGNORECASE | re.MULTILINE
)
_VERDICT_BLOCKED_RE = re.compile(
    r"^\s*Verdict:\s*Blocked\s*$", re.IGNORECASE | re.MULTILINE
)


def _verdict_from_text(text: str) -> ReviewVerdict | None:
    """Scan text for the first `Verdict: approved|rejected|blocked` line.

    Mirrors run_pipeline.py:803's `review_body_signals_approved` but
    generalized to all three verdict values. Returns None when no
    verdict line is found.
    """
    if _VERDICT_APPROVED_RE.search(text):
        return "approved"
    if _VERDICT_REJECTED_RE.search(text):
        return "rejected"
    if _VERDICT_BLOCKED_RE.search(text):
        return "blocked"
    return None
