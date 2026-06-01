"""Tests for the LiveProvider — subprocess-based author + reviewer with fallback.

These tests use tiny shell scripts as stand-ins for `claude` and `cursor-agent`.
That lets us exercise every code path (success, transient retry, exhaustion
fallback, timeout, missing CLI, verdict parsing) without ever calling a real
LLM or shipping a real network dependency.
"""

from __future__ import annotations

import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

from dualpass.config import AgentRole, AgentsConfig, StageConfig
from dualpass.providers import LiveProvider, LiveProviderError
from dualpass.providers.base import AuthorResult, StageContext
from dualpass.providers.live import (
    _resolve_artifact_path,
    _unwrap_json_envelope,
    _verdict_from_text,
    build_author_prompt,
    build_reviewer_prompt,
    parse_verdict,
)

# ── Test scaffolding ──────────────────────────────────────────────────────────


def _make_executable(script_path: Path, body: str) -> Path:
    """Write a shell script + chmod +x. Returns the path."""
    script_path.write_text("#!/bin/sh\n" + textwrap.dedent(body).lstrip())
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def _ctx(tmp_path: Path, stage_name: str = "research", round_number: int = 1) -> StageContext:
    units = tmp_path / "units"
    units.mkdir(parents=True, exist_ok=True)
    return StageContext(
        unit_id="demo-001",
        stage=StageConfig(
            name=stage_name,
            author_skill=f"skills/{stage_name}/SKILL.md",
            reviewer_skill=f"skills/{stage_name}/REVIEWER.md",
        ),
        round_number=round_number,
        units_dir=units,
        project_root=tmp_path,
    )


def _roles(*, author_cmd: str, reviewer_cmd: str, fallback_cmd: str | None = None) -> AgentsConfig:
    roles = {
        "author": AgentRole(name="author", command=author_cmd, timeout_seconds=15),
        "reviewer": AgentRole(
            name="reviewer",
            command=reviewer_cmd,
            timeout_seconds=15,
            exhaustion_patterns=("[resource_exhausted]",),
        ),
    }
    if fallback_cmd:
        roles["reviewer_fallback"] = AgentRole(
            name="reviewer_fallback",
            command=fallback_cmd,
            timeout_seconds=15,
            activate_after_consecutive_exhausted=2,
        )
    return AgentsConfig(roles=roles)


# ── Prompt building ───────────────────────────────────────────────────────────


def test_build_author_prompt_includes_skill_unit_round(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "spec", 3)
    skill = tmp_path / "skills" / "spec" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("AUTHOR-SKILL-CONTENT")
    prompt = build_author_prompt(ctx)
    assert "AUTHOR-SKILL-CONTENT" in prompt
    assert "'spec'" in prompt
    assert "'demo-001'" in prompt
    assert "round 3" in prompt


def test_build_author_prompt_with_missing_skill_falls_back_to_empty(tmp_path: Path) -> None:
    """Missing skill file is a warning, not a crash."""
    ctx = _ctx(tmp_path, "outline", 1)
    prompt = build_author_prompt(ctx)
    assert "outline" in prompt  # task block still present


def test_build_reviewer_prompt_embeds_artifact(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    artifact_path = ctx.units_dir / "research-artifact-v1.md"
    artifact_path.write_text("ARTIFACT-BODY-HERE")
    artifact = AuthorResult(artifact_path=artifact_path, served_by="x")
    prompt = build_reviewer_prompt(ctx, artifact)
    assert "ARTIFACT-BODY-HERE" in prompt
    assert "Verdict: approved" in prompt  # the contract is in the prompt


# ── Verdict parsing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("blah blah\nVerdict: approved", "approved"),
        ("Verdict: rejected", "rejected"),
        ("Verdict: blocked\n", "blocked"),
        ("notes\n\nVerdict: APPROVED with caveats", "approved"),
        # No verdict line → default to blocked (conservative).
        ("no verdict here", "blocked"),
        # Multiple verdicts — last one wins (so a reviewer that walks through
        # 'this looks rejected' then concludes 'Verdict: approved' is accepted).
        ("Verdict: rejected\nactually wait\nVerdict: approved", "approved"),
    ],
)
def test_parse_verdict_recognizes_valid_lines(text: str, expected: str) -> None:
    assert parse_verdict(text) == expected


# ── LiveProvider init guards ─────────────────────────────────────────────────


def test_constructor_raises_when_author_role_missing() -> None:
    cfg = AgentsConfig(roles={"reviewer": AgentRole(name="reviewer", command="true")})
    with pytest.raises(LiveProviderError, match="author"):
        LiveProvider(cfg)


def test_constructor_raises_when_reviewer_role_missing() -> None:
    cfg = AgentsConfig(roles={"author": AgentRole(name="author", command="true")})
    with pytest.raises(LiveProviderError, match="reviewer"):
        LiveProvider(cfg)


# ── End-to-end author + reviewer with fake CLIs ──────────────────────────────


def test_invoke_author_runs_subprocess_and_writes_artifact(tmp_path: Path) -> None:
    fake_author = _make_executable(
        tmp_path / "fake-author.sh",
        """
        # Echo a fixed response so we can verify capture worked.
        printf "## Stage Output\n\nThis is the synthesized output.\n"
        """,
    )
    cfg = _roles(author_cmd=str(fake_author), reviewer_cmd="true")
    provider = LiveProvider(cfg)
    ctx = _ctx(tmp_path)
    result = provider.invoke_author(ctx)
    body = result.artifact_path.read_text()
    assert "## Stage Output" in body
    # v1.0.4: diagnostic info goes to a .meta.json sidecar, not the artifact body
    assert "dualpass-served-by" not in body
    sidecar = result.artifact_path.with_suffix(".meta.json")
    assert sidecar.is_file()
    meta = json.loads(sidecar.read_text())
    assert meta["served_by"] == "author"
    assert result.extras["attempts"] == 1
    assert result.extras["returncode"] == 0


def test_invoke_reviewer_parses_verdict_from_real_subprocess(tmp_path: Path) -> None:
    fake_reviewer = _make_executable(
        tmp_path / "fake-reviewer.sh",
        """
        printf "Looks fine to me.\n\nVerdict: approved\n"
        """,
    )
    cfg = _roles(author_cmd="true", reviewer_cmd=str(fake_reviewer))
    provider = LiveProvider(cfg)
    ctx = _ctx(tmp_path)
    artifact_path = ctx.units_dir / "research-artifact-v1.md"
    artifact_path.write_text("something to review")
    artifact = AuthorResult(artifact_path=artifact_path, served_by="author")
    review = provider.invoke_reviewer(ctx, artifact)
    assert review.verdict == "approved"
    assert review.served_by == "reviewer"


def test_reviewer_unrecognizable_output_defaults_to_blocked(tmp_path: Path) -> None:
    fake_reviewer = _make_executable(
        tmp_path / "no-verdict.sh", "printf 'I forgot to write a verdict line.\n'"
    )
    cfg = _roles(author_cmd="true", reviewer_cmd=str(fake_reviewer))
    provider = LiveProvider(cfg)
    ctx = _ctx(tmp_path)
    artifact_path = ctx.units_dir / "research-artifact-v1.md"
    artifact_path.write_text("body")
    artifact = AuthorResult(artifact_path=artifact_path, served_by="author")
    review = provider.invoke_reviewer(ctx, artifact)
    assert review.verdict == "blocked"


# ── Cross-vendor fallback ────────────────────────────────────────────────────


def test_reviewer_falls_back_after_consecutive_exhaustion(tmp_path: Path) -> None:
    """Primary returns [resource_exhausted] twice in a row → fallback activates on attempt 3."""
    primary = _make_executable(
        tmp_path / "primary.sh",
        """
        # Primary always reports exhaustion.
        printf "[resource_exhausted]\n"
        exit 0
        """,
    )
    fallback = _make_executable(
        tmp_path / "fallback.sh",
        """
        printf "Verdict: approved\n"
        """,
    )
    cfg = _roles(
        author_cmd="true",
        reviewer_cmd=str(primary),
        fallback_cmd=str(fallback),
    )
    # activate_after_consecutive_exhausted is set to 2 by _roles when fallback_cmd is given.
    provider = LiveProvider(cfg)

    # Three rounds. The first two hit the primary (both report exhaustion); on the
    # third call the streak (2) meets the threshold (2), so we route to fallback.
    served = []
    verdicts = []
    for round_number in (1, 2, 3):
        ctx = _ctx(tmp_path, "research", round_number)
        artifact_path = ctx.units_dir / f"research-artifact-v{round_number}.md"
        artifact_path.write_text("body")
        artifact = AuthorResult(artifact_path=artifact_path, served_by="author")
        review = provider.invoke_reviewer(ctx, artifact)
        served.append(review.served_by)
        verdicts.append(review.verdict)

    assert served == ["reviewer", "reviewer", "reviewer_fallback"]
    # Primary returned no verdict → blocked; fallback gave approved.
    assert verdicts == ["blocked", "blocked", "approved"]


def test_reviewer_exhaustion_streak_resets_on_clean_response(tmp_path: Path) -> None:
    """If primary alternates exhaustion / success, streak should reset and fallback never trip."""
    flip_file = tmp_path / "flip.txt"
    flip_file.write_text("0")
    flip_script = _make_executable(
        tmp_path / "flip.sh",
        f"""
        count=$(cat {flip_file})
        if [ "$count" = "0" ]; then
          printf "[resource_exhausted]\n"
          echo "1" > {flip_file}
        else
          printf "Verdict: approved\n"
          echo "0" > {flip_file}
        fi
        """,
    )
    fallback = _make_executable(tmp_path / "fallback.sh", "printf 'Verdict: approved\n'")
    cfg = _roles(
        author_cmd="true",
        reviewer_cmd=str(flip_script),
        fallback_cmd=str(fallback),
    )
    provider = LiveProvider(cfg)

    served = []
    for round_number in (1, 2, 3, 4):
        ctx = _ctx(tmp_path, "research", round_number)
        artifact_path = ctx.units_dir / f"research-artifact-v{round_number}.md"
        artifact_path.write_text("body")
        artifact = AuthorResult(artifact_path=artifact_path, served_by="author")
        review = provider.invoke_reviewer(ctx, artifact)
        served.append(review.served_by)
    # Should never have hit the fallback — streak resets each clean response.
    assert all(s == "reviewer" for s in served), served


# ── Transient retry ──────────────────────────────────────────────────────────


def test_transient_retry_eventually_succeeds(tmp_path: Path) -> None:
    counter_file = tmp_path / "counter.txt"
    counter_file.write_text("0")
    script = _make_executable(
        tmp_path / "retry.sh",
        f"""
        count=$(cat {counter_file})
        next=$((count + 1))
        echo "$next" > {counter_file}
        if [ "$count" -lt "2" ]; then
          printf "ETIMEDOUT transient hiccup\n" >&2
          exit 1
        fi
        printf "success-on-attempt-$next\n"
        """,
    )
    cfg = AgentsConfig(
        roles={
            "author": AgentRole(
                name="author",
                command=str(script),
                timeout_seconds=10,
                transient_retries=3,
                transient_retry_delay_seconds=0,
                transient_retry_patterns=("ETIMEDOUT",),
            ),
            "reviewer": AgentRole(name="reviewer", command="true"),
        }
    )
    provider = LiveProvider(cfg)
    ctx = _ctx(tmp_path)
    result = provider.invoke_author(ctx)
    body = result.artifact_path.read_text()
    assert "success-on-attempt-3" in body
    assert result.extras["attempts"] == 3


def test_transient_retry_gives_up_after_max_attempts(tmp_path: Path) -> None:
    """If every attempt matches the transient pattern, we surface the failure."""
    always_fail = _make_executable(
        tmp_path / "always.sh",
        """
        printf "ETIMEDOUT every time\n" >&2
        exit 1
        """,
    )
    cfg = AgentsConfig(
        roles={
            "author": AgentRole(
                name="author",
                command=str(always_fail),
                timeout_seconds=5,
                transient_retries=2,
                transient_retry_delay_seconds=0,
                transient_retry_patterns=("ETIMEDOUT",),
            ),
            "reviewer": AgentRole(name="reviewer", command="true"),
        }
    )
    provider = LiveProvider(cfg)
    ctx = _ctx(tmp_path)
    result = provider.invoke_author(ctx)
    # Attempts = transient_retries + 1.
    assert result.extras["attempts"] == 3
    assert result.extras["returncode"] != 0


# ── Missing CLI / timeout ────────────────────────────────────────────────────


def test_missing_cli_returns_127_in_sidecar(tmp_path: Path) -> None:
    """v1.0.4: diagnostic returncode now lives in the .meta.json sidecar."""
    cfg = AgentsConfig(
        roles={
            "author": AgentRole(
                name="author", command="/this/binary/definitely/does/not/exist", timeout_seconds=2
            ),
            "reviewer": AgentRole(name="reviewer", command="true"),
        }
    )
    provider = LiveProvider(cfg)
    ctx = _ctx(tmp_path)
    result = provider.invoke_author(ctx)
    meta = json.loads(result.artifact_path.with_suffix(".meta.json").read_text())
    assert meta["returncode"] == 127


def test_subprocess_timeout_surfaces_in_sidecar(tmp_path: Path) -> None:
    """A subprocess that runs past timeout_seconds is killed and returncode 124 lands in the sidecar."""
    if os.name != "posix":
        pytest.skip("timeout assertion uses POSIX sleep semantics")
    slow = _make_executable(tmp_path / "slow.sh", "sleep 5\n")
    cfg = AgentsConfig(
        roles={
            "author": AgentRole(name="author", command=str(slow), timeout_seconds=1),
            "reviewer": AgentRole(name="reviewer", command="true"),
        }
    )
    provider = LiveProvider(cfg)
    ctx = _ctx(tmp_path)
    result = provider.invoke_author(ctx)
    meta = json.loads(result.artifact_path.with_suffix(".meta.json").read_text())
    assert meta["returncode"] == 124


# ── v1.0.2 — file-on-disk-first, JSON envelope unwrap, verdict-from-disk ─────


def _outcome_stub(stdout: str = "", served_by: str = "author"):
    """Minimal _Outcome for unit-testing the resolvers without a subprocess."""
    from dualpass.providers.live import _Outcome
    return _Outcome(stdout=stdout, stderr="", returncode=0, served_by=served_by, attempts=1)


def test_unwrap_json_envelope_extracts_result_field() -> None:
    """Claude --output-format json wraps the artifact in {"result": "..."}."""
    envelope = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"result":"---\\ntitle: hi\\n---\\n\\n# Body"}'
    )
    assert _unwrap_json_envelope(envelope) == "---\ntitle: hi\n---\n\n# Body"


def test_unwrap_json_envelope_passes_through_plain_markdown() -> None:
    plain = "---\ntitle: hi\n---\n\n# Body"
    assert _unwrap_json_envelope(plain) == plain


def test_unwrap_json_envelope_passes_through_malformed_json() -> None:
    """If text starts with '{' but isn't valid JSON, return unchanged."""
    malformed = "{ not valid json at all }"
    assert _unwrap_json_envelope(malformed) == malformed


def test_unwrap_json_envelope_passes_through_object_without_result_field() -> None:
    """JSON object without a string `result` field is returned unchanged."""
    no_result = '{"type":"info","duration_ms":123}'
    assert _unwrap_json_envelope(no_result) == no_result


def test_unwrap_json_envelope_handles_leading_whitespace() -> None:
    envelope = '\n  {"result":"hello"}'
    assert _unwrap_json_envelope(envelope) == "hello"


def test_resolve_artifact_path_uses_file_on_disk_when_present(tmp_path: Path) -> None:
    """v1.0.2: if the agent wrote a file via its own Write tool, that file
    wins. v1.0.4: artifact body is pure markdown; meta in sidecar."""
    expected = tmp_path / "research-artifact-v1.md"
    expected.write_text("---\ntitle: real\n---\n\n# Body the agent wrote", encoding="utf-8")
    outcome = _outcome_stub(stdout="this was the stdout summary, not the artifact")
    _resolve_artifact_path(expected, outcome)
    body = expected.read_text()
    assert body.startswith("---")
    assert "# Body the agent wrote" in body
    assert "this was the stdout summary" not in body
    assert "dualpass-served-by" not in body
    sidecar = expected.with_suffix(".meta.json")
    assert sidecar.is_file()
    meta = json.loads(sidecar.read_text())
    assert meta["served_by"] == "author"


def test_resolve_artifact_path_falls_back_to_stdout_when_no_file(tmp_path: Path) -> None:
    """If no file on disk, write the stdout as the artifact (old behavior)."""
    expected = tmp_path / "research-artifact-v1.md"
    outcome = _outcome_stub(stdout="---\ntitle: hi\n---\n\n# Stdout-streamed body")
    _resolve_artifact_path(expected, outcome)
    body = expected.read_text()
    assert "# Stdout-streamed body" in body


def test_resolve_artifact_path_unwraps_json_envelope_already_on_disk(tmp_path: Path) -> None:
    """If a previous run wrote a JSON envelope to disk (the v1.0.1 bug),
    unwrap it on the next pass. v1.0.4: zero inline headers; meta in sidecar."""
    expected = tmp_path / "research-artifact-v1.md"
    envelope = (
        '<!-- dualpass-served-by: author -->\n'
        '<!-- dualpass-attempts: 1 -->\n'
        '<!-- dualpass-returncode: 0 -->\n'
        '{"result":"---\\ntitle: rescued\\n---\\n\\n# Real body"}'
    )
    expected.write_text(envelope, encoding="utf-8")
    outcome = _outcome_stub(stdout="ignored")
    _resolve_artifact_path(expected, outcome)
    body = expected.read_text()
    assert "# Real body" in body
    # v1.0.4: no inline diagnostic headers at all — sidecar only.
    assert "dualpass-served-by" not in body
    assert body.startswith("---")


def test_resolve_artifact_path_strips_stacked_diagnostic_headers(tmp_path: Path) -> None:
    """v1.0.4: an artifact carrying multiple stacked diagnostic-header triplets
    (the v1.0.3 chunk-77a-v103 spec-stage failure mode where claude included
    additional copies in its own output during revision rounds) gets all of
    them stripped, not just the first."""
    expected = tmp_path / "spec-artifact-v6.md"
    stacked = (
        '<!-- dualpass-served-by: author -->\n'
        '<!-- dualpass-attempts: 1 -->\n'
        '<!-- dualpass-returncode: 0 -->\n'
        '<!-- dualpass-served-by: author -->\n'
        '<!-- dualpass-attempts: 1 -->\n'
        '<!-- dualpass-returncode: 0 -->\n'
        '---\n'
        'title: spec\n'
        '---\n\n'
        '# Body\n'
    )
    expected.write_text(stacked, encoding="utf-8")
    outcome = _outcome_stub(stdout="ignored")
    _resolve_artifact_path(expected, outcome)
    body = expected.read_text()
    assert "dualpass-served-by" not in body
    assert body.startswith("---")
    assert "# Body" in body


def test_resolve_artifact_path_strips_preamble_before_frontmatter(tmp_path: Path) -> None:
    """v1.0.4: claude's narration preamble between header location and
    frontmatter is stripped — the chunk-77a-v103 spec-stage round-1-through-5
    failure mode where claude couldn't override its narration instinct round
    over round even when the gate feedback named it directly."""
    expected = tmp_path / "outline-artifact-v1.md"
    with_preamble = (
        '<!-- dualpass-served-by: author -->\n'
        '<!-- dualpass-attempts: 1 -->\n'
        '<!-- dualpass-returncode: 0 -->\n'
        'Now I have all the context. Let me produce the outline.\n\n'
        '---\n'
        'title: outline\n'
        '---\n\n'
        '# Body\n'
    )
    expected.write_text(with_preamble, encoding="utf-8")
    outcome = _outcome_stub(stdout="ignored")
    _resolve_artifact_path(expected, outcome)
    body = expected.read_text()
    assert "Now I have all the context" not in body
    assert body.startswith("---")


def test_resolve_artifact_path_leaves_h1_bold_artifacts_untouched(tmp_path: Path) -> None:
    """v1.0.4: stages using H1 + bold-prefix metadata (outline/spec/prompt/
    audit/handoff per the v1.0.3 bundled config) have no YAML frontmatter
    delimiter, so the preamble-strip should NOT activate — every line is
    legitimate artifact content."""
    expected = tmp_path / "outline-artifact-v1.md"
    h1_bold = (
        '# Chunk 77a Outline — Image-OCR Ingestion\n\n'
        '**Phase:** 9\n'
        '**Module:** Discovery\n\n'
        '## 1. Some section\n'
    )
    expected.write_text(h1_bold, encoding="utf-8")
    outcome = _outcome_stub(stdout="ignored")
    _resolve_artifact_path(expected, outcome)
    body = expected.read_text()
    # All content preserved verbatim — no `---` to anchor a strip.
    assert body.startswith("# Chunk 77a Outline")
    assert "**Phase:** 9" in body


def test_resolve_artifact_path_treats_empty_file_as_no_file(tmp_path: Path) -> None:
    """A zero-byte file should not count as 'agent wrote something'."""
    expected = tmp_path / "research-artifact-v1.md"
    expected.write_text("", encoding="utf-8")
    outcome = _outcome_stub(stdout="# Stdout content")
    _resolve_artifact_path(expected, outcome)
    body = expected.read_text()
    assert "# Stdout content" in body


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Verdict: Approved", "approved"),
        ("Verdict: approved", "approved"),
        ("# Review\n\nVerdict: Rejected\n", "rejected"),
        ("Verdict: Blocked", "blocked"),
        ("no verdict anywhere", None),
    ],
)
def test_verdict_from_text_parses_review_body(text: str, expected) -> None:
    assert _verdict_from_text(text) == expected


def test_invoke_author_preserves_agent_written_file(tmp_path: Path) -> None:
    """Integration: when the author CLI writes the artifact via its own tools
    AND prints a status summary to stdout, the file-on-disk content wins."""
    # Pre-populate the artifact as the agent would have via its Write tool.
    ctx = _ctx(tmp_path)
    artifact = ctx.units_dir / "research-artifact-v1.md"
    artifact.write_text(
        "---\ntitle: Real artifact from agent\n---\n\n# Body the agent wrote with its own Write tool\n",
        encoding="utf-8",
    )

    cfg = AgentsConfig(
        roles={
            "author": AgentRole(name="author", command="echo Wrote artifact. Status complete.", timeout_seconds=15),
            "reviewer": AgentRole(name="reviewer", command="true", timeout_seconds=15),
        }
    )
    provider = LiveProvider(cfg)
    result = provider.invoke_author(ctx)
    body = result.artifact_path.read_text()
    assert "Body the agent wrote with its own Write tool" in body
    assert "Wrote artifact. Status complete." not in body
    # v1.0.4: artifact body is pure markdown; no inline diagnostic headers.
    assert "dualpass-served-by" not in body
    sidecar = result.artifact_path.with_suffix(".meta.json")
    assert sidecar.is_file()
