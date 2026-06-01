# CONFIG-REFERENCE

Every config file, every field. dualpass is configured by a small set of YAML/JSON files in your project's `config/` directory. The harness validates all configs at startup via `jsonschema`; bad config = clear error message, not silent misbehavior.

> **Status:** v1.0.0. All schemas described below are validated by `dualpass config validate` and at startup by every command that loads config.

---

## File layout

A `dualpass init`-scaffolded project has:

```
config/
├── dualpass.json        # top-level harness behavior (required)
├── agents.yaml          # named agent roles → CLI templates (required)
├── stages.yaml          # ordered stage chain (required)
├── permissions.yaml     # safety tiers (required)
└── gates/               # per-stage gate plugin overrides (optional)
```

> **Note:** `src/dualpass/context.py` currently uses a fixed set of project-doc sources (`PROJECT.md`, `DECISIONS.md`, `BACKLOG.md`, `DOC-MAP.md` under `docs/_project/`). Per-project override of these sources is a future-release item; no `context-sources.yaml` is loaded today.

---

## `config/dualpass.json` — top-level harness config

```json
{
  "$schema": "https://github.com/Chris-Rebentisch/dualpass/raw/main/schemas/dualpass.json",
  "schema_version": "0.1.0",
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
| `schema_version` | string | (required) | Config-file schema version (e.g. `"0.1.0"`). Tracks the shape of the config files; distinct from the dualpass package version reported by `dualpass --version`. |
| `project_name` | string | (required) | Display name in `dualpass status` output |
| `unit_id_pattern` | string | `<slug>` | One of `<slug>`, `<int>`, `<int>[a-z]?`. Validates `--unit` arg shape. |
| `max_revision_rounds` | object | `{default: 6}` | Cap on reviewer loops per stage. Per-stage overrides allowed. |
| `breakpoints` | object | all false | Stage halt points. `true` = pause. There is no `dualpass set-breakpoint` subcommand; edit this map (or the per-stage `breakpoint_default` in `stages.yaml`) directly. |
| `circuit_breaker.max_no_progress_relaunches` | int | 3 | Trip threshold |
| `single_flight_lockfile` | bool | true | Whether watchers respect `<unit>-pipeline.lock.json`. Leave true. |
| `auto_lock_finals` | bool | false | When `true`, the controller copies an approved `<stage>-v<N>.md` to its `<stage>-v<N>-FINAL.md` sibling once the reviewer approves. Default `false` keeps draft-to-FINAL promotion an explicit operator step. |

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
      - check-acceptance-criteria-wording
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

# `forbidden_actions` is a mapping of category name -> list of regex strings.
# The schema (src/dualpass/schemas/permissions.json) enforces this shape; the
# example below mirrors the bundled coding-agent example.
forbidden_actions:
  destructive_git:
    - "push --force-with-lease.*main"
    - "reset --hard.*origin"
    - "branch -D main"
  mass_delete:
    - "rm -rf /"
    - "rm -rf ~"
  credential_exfil:
    - "cat.*\\.env"
    - "cat.*credentials"

audit_log: .dualpass-state/permission-audit.log
```

| Field | Purpose |
|---|---|
| `default_posture` | Harness behavior when an agent proposes a mutating action |
| `mutating_actions_require_approval` | Master switch; even `auto-accept-safe` honors this for write actions |
| `opt_in_skips` | Environment-variable opt-outs. All default to `false` (gate active). Setting to `true` skips the corresponding gate. |
| `forbidden_actions` | Regex blocklist; matched actions are denied regardless of posture |
| `audit_log` | Where every permission decision is recorded |

When `auto_lock_finals: true` is set in `dualpass.json`, the controller copies an approved `<stage>-v<N>.md` to its `<stage>-v<N>-FINAL.md` sibling on reviewer approval. The default is `false` (operator promotes drafts to FINAL manually). The flag does NOT bypass permission checks — it only promotes the file after the reviewer's approval lands.

---

## Project-doc context sources (fixed in v1)

`src/dualpass/context.py` builds the stage-context bundle from a fixed set of project docs:

- `docs/_project/PROJECT.md`
- `docs/_project/DECISIONS.md`
- `docs/_project/BACKLOG.md`
- `docs/_project/DOC-MAP.md`

Plus the predecessor stage's FINAL artifact and (for outline / spec) a small precedent cache of recent peer artifacts. These sources are hard-coded in v1; operator-overridable context curation (via a `config/context-sources.yaml` file or similar) is on the v1.1 roadmap.

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
  - .dualpass-state/{unit_id}/research-FINAL.md
  - .dualpass-state/{unit_id}-stage-context.md
  - .dualpass-state/{unit_id}-precedent-cache.md
artifacts_produced:
  - .dualpass-state/{unit_id}/outline-v{round}.md
success_criteria:
  - Outline has §1–§N matching the template at references/outline-output-format.md
  - Every line citation resolves against the real source
  - No invented identifiers (cite real symbols and files only)
---

# Outline-author skill

[Free-form instructions for the agent. Markdown. The agent reads this verbatim.]

## Process

1. Read `.dualpass-state/{unit_id}/research-FINAL.md`.
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

Built-in gates ship with dualpass and are auto-registered the moment the `dualpass` package is imported (`src/dualpass/gates/builtins.py` calls `register_gate(...)` at import time). Reference any of them by name from `stages.yaml` without further setup:

| Gate name | Purpose |
|---|---|
| `check-frontmatter` | Confirms the artifact begins with a YAML frontmatter block delimited by `---` fences and contains every field listed in the gate's `required_fields` config (defaults to `["title"]`). Catches malformed or missing frontmatter before a reviewer round is spent on it. |
| `check-line-citations` | Walks every `path:line` reference in the artifact body and resolves the file. When the gate's `verify_lines` config is set, it also checks that each cited line number is in range for the target file. Stale citations are flagged with file and line; the diagnostic caps at the first 10. |
| `check-single-flight` | Refuses to proceed when the unit's pipeline lockfile is held by another process. Passes when the lock is absent or held by the current PID; fails with the foreign PID in the diagnostic. Prevents two orchestrators from racing on the same unit. |
| `check-marker-frontmatter` | Validates the build-complete marker the author stage emits at `.dualpass-state/<unit>-build-complete.md`. Surfaces parse errors here rather than letting the controller fail later when it tries to read `exit_signal` / `blocker_kind`. |
| `check-acceptance-criteria-wording` | Scans only inside acceptance-criteria sections of the artifact and flags brittle exact-count phrasings (e.g. "exactly 12 tests"). Loose wording ("at least 12 tests covering …") preserves the same intent without coupling the criterion to incidental edits. |

Project-level gates live in `gates/<stage>/<gate-name>.{sh,py}`. Reference them in `stages.yaml` by file basename. The harness passes the artifact path as `$1` and the unit id as `$2`.

---

## Environment variables

dualpass honors a small set of env vars for opt-out / debugging:

| Variable | Purpose |
|---|---|
| `DUALPASS_SKIP_PRE_CODE_INDEPENDENT_REVIEW` | Skip the pre-code independent review subprocess |
| `DUALPASS_SKIP_ARTIFACT_PREFLIGHT` | Skip the artifact-preflight gate batch |
| `DUALPASS_SKIP_VERIFY_ARTIFACT_CLAIMS` | Skip the line-citation containment check |
| `DUALPASS_REQUIRE_SERVICES` | Force live-service-dependent tests to run (used in CI) |
| `DUALPASS_STATE_DIR` | Override `.dualpass-state/` location (rarely needed) |

All skips default to `false` (gate active). Setting any to `true` is your acknowledgment that you understand the safety implication.

> **Note on inherited doctrine.** Two patterns from the harness's lineage — a "no-progress halt-and-remediate" guard (detects when the reviewer keeps rejecting an artifact whose source-tree hash has not changed) and a cumulative-count cascade (verifies counts cited in a spec against live codebase probes) — are documented at the reviewer-skill level (see `skills/audit/REVIEWER.md`). They are NOT enforced by a built-in gate in v1. A gate plugin that automates either is a v1.1 candidate.

---

## Validation

```bash
dualpass config validate
```

Validates all config files in your project's `config/` against their JSON schemas. Reports **every** error in one pass (not just the first) using `file:path: message` format, and exits non-zero on any issue. Also runs cross-file invariants: stage `requires_predecessor` must point at a stage defined earlier; `breakpoints` keys must match real stage names; `max_revision_rounds` overrides must reference real stages.
