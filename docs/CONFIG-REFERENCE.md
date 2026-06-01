# CONFIG-REFERENCE

Every config file, every field. dualpass is configured by a small set of YAML/JSON files in your project's `config/` directory. The harness validates all configs at startup via `jsonschema`; bad config = clear error message, not silent misbehavior.

> **Status:** v0.1.0a0. Schemas below are the v1 target. Where validation is not yet implemented, this doc says so.

---

## File layout

A `dualpass init`-scaffolded project has:

```
config/
├── dualpass.json        # top-level harness behavior (required)
├── agents.yaml          # named agent roles → CLI templates (required)
├── stages.yaml          # ordered stage chain (required)
├── permissions.yaml     # safety tiers (required)
├── context-sources.yaml # which docs feed into stage context (optional)
└── gates/               # per-stage gate plugin overrides (optional)
```

---

## `config/dualpass.json` — top-level harness config

```json
{
  "$schema": "https://github.com/Chris-Rebentisch/dualpass/raw/main/schemas/dualpass.json",
  "version": "0.1.0",
  "project_name": "my-project",
  "unit_id_pattern": "<slug>",
  "max_revision_rounds": {
    "default": 6,
    "spec": 8
  },
  "breakpoints": {
    "research": false,
    "outline": false,
    "spec": false,
    "code": true,
    "audit": false,
    "handoff": false,
    "close": false
  },
  "circuit_breaker": {
    "max_no_progress_relaunches": 3,
    "progress_signal": "build_marker_hash"
  },
  "single_flight_lockfile": true,
  "auto_lock_finals": true
}
```

| Field | Type | Default | Purpose |
|---|---|---|---|
| `version` | string | (required) | dualpass schema version this config targets |
| `project_name` | string | (required) | Display name in `dualpass status` output |
| `unit_id_pattern` | string | `<slug>` | One of `<slug>`, `<int>`, `<int>[a-z]?`. Validates `--unit` arg shape. |
| `max_revision_rounds` | object | `{default: 6}` | Cap on reviewer loops per stage. Per-stage overrides allowed. |
| `breakpoints` | object | all false | Stage halt points. `true` = pause; flip with `dualpass set-breakpoint`. |
| `circuit_breaker.max_no_progress_relaunches` | int | 3 | Trip threshold |
| `single_flight_lockfile` | bool | true | Whether watchers respect `<unit>-pipeline.lock.json`. Leave true. |
| `auto_lock_finals` | bool | true | Whether the controller auto-copies approved drafts to `-FINAL.md` |

---

## `config/agents.yaml` — named agent roles

```yaml
roles:
  author:
    command: "claude --dangerously-skip-permissions --model claude-opus-4-6 -p {prompt} --output-format json"
    timeout_seconds: 5400
    transient_retries: 3
    transient_retry_delay_seconds: 10
    transient_retry_patterns:
      - "ETIMEDOUT"
      - "[unavailable]"
      - "ECONNRESET"

  reviewer:
    command: "cursor-agent --yolo --trust --approve-mcps -p {prompt} --output-format json"
    timeout_seconds: 5400
    transient_retries: 3
    transient_retry_delay_seconds: 10

  reviewer_fallback:
    command: "env -u ANTHROPIC_API_KEY CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000 claude --dangerously-skip-permissions --model claude-opus-4-6 -p {prompt} --output-format json"
    timeout_seconds: 5400
    activate_after_consecutive_exhausted: 3
    exhaustion_patterns:
      - "[resource_exhausted]"
      - "rate_limit_exceeded"
```

### CLI-template contract

Every role's `command` string is interpolated with one variable: `{prompt}`. The CLI must:

- Accept the prompt as either `-p <prompt>` or via stdin (specify the variant by template shape).
- Return either plain text or JSON (set `--output-format` accordingly).
- Exit 0 on success.
- Exit non-zero AND print a recognizable error pattern on transient failures (so `transient_retry_patterns` can match).

| Field | Required | Purpose |
|---|---|---|
| `command` | yes | Templated CLI invocation |
| `timeout_seconds` | no (default 5400 = 90 min) | Per-invocation cap |
| `transient_retries` | no (default 3) | How many times to retry on transient_retry_patterns match |
| `transient_retry_delay_seconds` | no (default 10) | Wait between retries |
| `transient_retry_patterns` | no | Regex patterns in stderr/stdout that mark a retryable failure |
| `activate_after_consecutive_exhausted` | only on `*_fallback` roles | How many consecutive exhaustion failures trigger fallback |
| `exhaustion_patterns` | only on `*_fallback` roles | Patterns marking exhaustion (distinct from transient) |

---

## `config/stages.yaml` — ordered stage chain

```yaml
stages:
  - name: research
    author_skill: skills/research/SKILL.md
    reviewer_skill: skills/research/REVIEWER.md
    preflight_gates:
      - check-frontmatter
    max_rounds: 6
    breakpoint_default: false

  - name: outline
    author_skill: skills/outline/SKILL.md
    reviewer_skill: skills/outline/REVIEWER.md
    preflight_gates:
      - check-frontmatter
      - check-line-citations
    max_rounds: 6
    requires_predecessor: research

  - name: spec
    author_skill: skills/spec/SKILL.md
    reviewer_skill: skills/spec/REVIEWER.md
    dual_pass_reviewer: true        # structural + substantive in parallel
    preflight_gates:
      - check-frontmatter
      - check-line-citations
      - check-ac1-wording
    max_rounds: 8
    requires_predecessor: outline

  # ... continue for prompt, code, audit, handoff
```

| Field | Required | Purpose |
|---|---|---|
| `name` | yes | Stage identifier; used in artifact filenames and CLI args |
| `author_skill` | yes | Path to the SKILL.md the author agent loads |
| `reviewer_skill` | yes | Path to the SKILL.md the reviewer agent loads |
| `dual_pass_reviewer` | no (default false) | If true, two reviewers run in parallel (structural + substantive) and both must approve |
| `preflight_gates` | no | Gate plugin names to run before reviewer |
| `max_rounds` | no (default from dualpass.json) | Per-stage override |
| `requires_predecessor` | no | Block stage launch unless the named stage has a `-FINAL.md` |
| `breakpoint_default` | no (default from dualpass.json) | Override the default breakpoint state for this stage |

---

## `config/permissions.yaml` — safety tiers

```yaml
default_posture: ask              # ask | auto-accept-safe | plan-only | bypass

mutating_actions_require_approval: true

opt_in_skips:
  DUALPASS_SKIP_PRE_CODE_INDEPENDENT_REVIEW: false
  DUALPASS_SKIP_ARTIFACT_PREFLIGHT: false
  DUALPASS_SKIP_VERIFY_ARTIFACT_CLAIMS: false
  DUALPASS_SKIP_COUNT_REBASE: false

forbidden_actions:
  - destructive_git: ["push --force-with-lease.*main", "reset --hard.*origin", "branch -D main"]
  - mass_delete: ["rm -rf /", "rm -rf ~"]

audit_log: .dualpass-state/permission-audit.log
```

| Field | Purpose |
|---|---|
| `default_posture` | Harness behavior when an agent proposes a mutating action |
| `mutating_actions_require_approval` | Master switch; even `auto-accept-safe` honors this for write actions |
| `opt_in_skips` | Environment-variable opt-outs. All default to `false` (gate active). Setting to `true` skips the corresponding gate. |
| `forbidden_actions` | Regex blocklist; matched actions are denied regardless of posture |
| `audit_log` | Where every permission decision is recorded |

The `auto_lock_finals: true` setting in `dualpass.json` does NOT bypass permission checks — it only auto-promotes approved drafts to FINAL after the reviewer's approval lands.

---

## `config/context-sources.yaml` — project-doc context curation

```yaml
project_docs:
  - path: docs/_project/PROJECT.md
    inject_into_stages: [research, outline, spec]
    slice: full
  - path: docs/_project/DECISIONS.md
    inject_into_stages: [outline, spec, code, audit]
    slice: section
    section: "## Active decisions"
  - path: docs/_project/BACKLOG.md
    inject_into_stages: [research, outline]
    slice: row
    row_selector: "unit == {unit_id}"

predecessor_finals:
  enabled: true
  compress_to_max_tokens: 8000

precedent_cache:
  enabled_for_stages: [outline, spec]
  count: 3
  ratified_only: true
```

This file controls what `src/dualpass/context.py` reads when building the stage-context bundle. If omitted, dualpass uses sensible defaults (predecessor FINAL + project docs full-text).

---

## Stage skills — Anthropic skill format

Every stage's `author_skill` and `reviewer_skill` is a directory containing a `SKILL.md` file with YAML frontmatter, plus optional `references/` for in-context resources.

```markdown
---
name: outline-author
description: Author the outline for one unit, reading from research-FINAL and project docs.
sub_agents:
  - name: precedent-summarizer
    prompt: "Summarize the prior 2 outline FINALs in 300 words each, focused on structural patterns."
    tools: [read_file]
    context_cap_tokens: 6000
context_sources:
  - units/{unit_id}/research-FINAL.md
  - .dualpass-state/{unit_id}-stage-context.md
  - .dualpass-state/{unit_id}-precedent-cache.md
artifacts_produced:
  - units/{unit_id}/outline-v{round}.md
success_criteria:
  - Outline has §1–§N matching template at references/outline-output-format.md
  - Every line citation resolves against the real source
  - No D-numbers or invented identifiers
---

# Outline-author skill

[Free-form instructions for the agent. Markdown. The agent reads this verbatim.]

## Process

1. Read `units/{unit_id}/research-FINAL.md`.
2. Read `.dualpass-state/{unit_id}-stage-context.md`.
3. ...

## Constraints

- Do NOT invent identifiers. Cite real symbols/files only.
- ...
```

The `name`, `description`, and `artifacts_produced` fields are required. Everything else is optional. Skills can be customized freely — drop your `SKILL.md` into Claude (or whatever model) and ask it to adjust to your preferences.

See `examples/coding-agent/skills/` for working examples generalized from a real production pipeline.

---

## Gate plugins — `src/dualpass/gates/` and project-level `gates/`

A gate is an executable (shell script or Python script) that exits 0 on success, non-zero on failure. The harness invokes it before the reviewer launches and uses its exit code + stderr as the gate result.

Built-in gates (ship with dualpass):

| Gate name | Purpose |
|---|---|
| `check-frontmatter` | Validates artifact YAML frontmatter against the stage's expected schema |
| `check-line-citations` | Verifies every `path:line` citation resolves; uses substring containment for short tokens (length ≤ 40) and difflib similarity for phrases |
| `check-single-flight` | Confirms no other orchestrator is running for this unit |
| `check-marker-frontmatter` | Validates build-complete marker frontmatter (status, exit_signal, blocker_kind) |
| `check-ac1-wording` | (spec-stage only) Rejects "exactly N" phrasings that overconstrain audit gates |

Project-level gates live in `gates/<stage>/<gate-name>.{sh,py}`. Reference them in `stages.yaml` by file basename. The harness passes the artifact path as `$1` and the unit id as `$2`.

---

## Environment variables

dualpass honors a small set of env vars for opt-out / debugging:

| Variable | Purpose |
|---|---|
| `DUALPASS_SKIP_PRE_CODE_INDEPENDENT_REVIEW` | Skip the pre-code independent review subprocess |
| `DUALPASS_SKIP_ARTIFACT_PREFLIGHT` | Skip the artifact-preflight gate batch |
| `DUALPASS_SKIP_VERIFY_ARTIFACT_CLAIMS` | Skip the line-citation containment check |
| `DUALPASS_SKIP_COUNT_REBASE` | Skip the cumulative-count cascade (only relevant if your project has one) |
| `DUALPASS_REQUIRE_SERVICES` | Force live-service-dependent tests to run (used in CI) |
| `DUALPASS_STATE_DIR` | Override `.dualpass-state/` location (rarely needed) |

All skips default to `false` (gate active). Setting any to `true` is your acknowledgment that you understand the safety implication.

---

## Validation

```bash
uv run dualpass config validate
```

Validates all config files in your project's `config/` against their JSON schemas. Reports first error and exits non-zero on any issue.

(v0.1.0a0 status: validator not yet implemented.)
