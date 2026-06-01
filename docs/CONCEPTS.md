# CONCEPTS

dualpass is a thin, opinionated harness around the nine canonical components of a production LLM (Large Language Model — the neural network) agent system, with one feature elevated to first-class status: **cross-vendor independent review**.

This document explains what each component does, where it lives in dualpass, and the specific reliability patterns dualpass ships as built-ins. If you are new to agent harness engineering, read [the foundations document](https://github.com/Chris-Rebentisch/dualpass/blob/main/docs/CONCEPTS.md) first — this file assumes you know what tool calling, RAG, and a context window are.

---

## 0. The frame

A production-grade agent is mostly deterministic software with LLM calls inserted at narrow decision points — not an open-ended "prompt + tools + loop-until-goal" autonomy engine. Anthropic engineering and HumanLayer (Dex Horthy) both document this independently: the naive loop plateaus around 70–80% reliability and refuses to go higher without re-architecture.

dualpass is the deterministic software. The LLM calls happen at exactly two points per stage:

1. **The author agent** produces an artifact.
2. **The reviewer agent** judges that artifact.

That cycle is the universe of LLM invocation in dualpass. Everything else is plain code.

---

## 1. Model — `config/agents.yaml`

Agents are named roles, each pointing at a CLI invocation template. dualpass does not import vendor SDKs; it shells out to whatever CLI you have installed.

```yaml
# config/agents.yaml
roles:
  author:
    command: "claude --dangerously-skip-permissions --model claude-opus-4-6 -p {prompt} --output-format json"
    timeout_seconds: 5400
    transient_retries: 3
    transient_retry_delay_seconds: 10
  reviewer:
    command: "cursor-agent --yolo --trust --approve-mcps -p {prompt} --output-format json"
    timeout_seconds: 5400
    transient_retries: 3
  reviewer_fallback:
    command: "claude --dangerously-skip-permissions --model claude-opus-4-6 -p {prompt} --output-format json"
    timeout_seconds: 5400
    activate_after_consecutive_exhausted: 3
```

**Why CLI templates instead of SDKs.** The LLM ecosystem fragments faster than any SDK can keep up. As long as a model has a CLI that accepts a prompt and returns text (or JSON), dualpass can drive it. You bring the CLI; dualpass brings the orchestration.

**Why cross-vendor by default.** Self-evaluation bias is well-documented (Anthropic engineering; NeurIPS 2024 work on self-preference). A model grading its own work confidently praises mediocre output. A *different vendor's* model — trained on different data, with different RLHF signal, with different failure modes — catches what a same-vendor reviewer misses. dualpass's default config makes this the easy path, not the path you have to remember to configure.

**Defaults.** `claude` as author, `cursor-agent` as reviewer, `claude` as reviewer-fallback. Swap freely.

---

## 2. Controller — `src/dualpass/controller.py`

The deterministic loop driver. The controller is plain Python code, not an LLM. It owns:

- **Loop control.** When to retry, when to bail, when to advance. Hard limits on revision rounds per stage (default: 6, configurable).
- **Single-flight protection.** A lockfile at `.dualpass-state/<unit>-pipeline.lock.json` prevents two orchestrators from racing on the same unit. Watchers respect this lock and skip triggers when it's present.
- **Circuit breaker.** If three consecutive auto-relaunches produce no measurable progress (same exit-signal, same artifact hash), the controller halts with a structured diagnostic at `.dualpass-state/<unit>-circuit-breaker-tripped.md`. The architect resets by deleting `.dualpass-state/<unit>-circuit-state.json`.
- **Auto-relaunch.** When a stage emits `exit_signal: continue`, the controller immediately relaunches a fresh subprocess — no architect intervention needed. Bounded by the circuit breaker.
- **Stage breakpoints.** Configurable per-stage halt points let you stop the pipeline between phases without killing the process.

The controller never asks the LLM for permission to halt. The LLM proposes; the controller disposes.

---

## 3. Tools — stage skills + gate plugins

Two distinct surfaces:

**Stage skills** (Anthropic skill format — `SKILL.md` + frontmatter, plus optional `references/` directory). A skill is the contract between dualpass and the author/reviewer agent for one stage. The skill defines the agent's prompt, the artifacts it produces, the success criteria. See [docs/CONFIG-REFERENCE.md](CONFIG-REFERENCE.md#stage-skills) for the format.

**Gate plugins** (`src/dualpass/gates/<stage>/` or project-level `gates/<stage>/`). Deterministic scripts that run before each stage's reviewer launches. They probe the artifact for mechanical correctness — line-citation validation, schema-claim checks, frontmatter linting. A gate failure is grounds for auto-revision; the agent gets the gate's diagnostic in its next prompt.

**Why both.** Stage skills handle the *judgment* surface — what does "good" look like for this stage. Gate plugins handle the *correctness* surface — does the artifact even satisfy mechanical constraints. Keep them separate; bugs are easier to localize when they don't overlap.

---

## 4. Context window — `src/dualpass/context.py`

The context window is the working memory of one LLM call: system prompt + conversation history + retrieved documents + tool results. It is finite and precious.

dualpass curates context explicitly via two builders:

- **Stage context bundle.** At the start of every stage, dualpass writes `.dualpass-state/<unit>-stage-context.md` — a compressed blob containing the relevant project-doc slices (PROJECT, DECISIONS, BACKLOG, DOC-MAP), the immediate-predecessor stage's FINAL artifact, and any stage-specific tech facts. Skills bootstrap from this file first; deep-reading canonical docs only happens when verification requires it.
- **Precedent cache.** For stages whose author benefits from seeing recent peer artifacts (typically outline and spec), dualpass writes `.dualpass-state/<unit>-precedent-cache.md` — compressed extracts of the 2–3 most recent ratified peer stages.

**Why this matters.** Context degradation on long tasks is the #1 documented failure mode. Models are model-specific: stronger models tolerate longer context, weaker models need full resets bridged by external artifacts. dualpass's context strategy is configurable per project; do not hardcode the assumption that compaction is sufficient.

---

## 5. Memory — `.dualpass-state/` + project docs

Two kinds of memory, and they are different:

- **In-context memory** — what's currently in the model's context window. Lost on every reset.
- **Out-of-context memory** — durable files on disk. Survives across sessions.

dualpass's out-of-context memory has three tiers:

| Tier | Lives at | Purpose |
|---|---|---|
| Ephemeral | `.dualpass-state/<unit>-*.{md,json,log}` | Per-unit build state: locks, markers, logs, frontmatter |
| Per-unit artifacts | `units/<unit-id>/<stage>-v{N}{,-FINAL}.md` | The stage outputs themselves; the FINAL version is the ratified artifact |
| Project-level | `docs/_project/{PROJECT,DECISIONS,BACKLOG,DOC-MAP,RUNBOOK}.md` | Cross-unit truth: decisions registry, backlog status, doc map |

**Why structured external artifacts beat in-context compaction.** Compaction summarizes *what* happened but loses *why*. Project-doc updates and unit artifacts preserve *why*. Anthropic's effective-harness research is explicit: cross-session continuity for long-running agents requires explicit artifacts (progress files, git commits, JSON feature lists), not compaction alone.

dualpass writes per-stage build markers to `.dualpass-state/<unit>-build-complete.md` with required frontmatter:

```yaml
---
unit: demo-001
stage: code
status: complete            # partial | complete | blocked
exit_signal: continue       # stop | continue | escalate
blocker_kind: null          # null | architectural | infrastructure | spec_defect
artifacts_produced:
  - units/demo-001/code-v3-FINAL.md
---
```

The controller parses this on stage exit and acts on `exit_signal`.

---

## 6. Sub-agents — declared per stage skill

A sub-agent is a separately-invoked instance of the agent loop running in its own isolated context window. The parent agent calls the sub-agent as if it were a tool; the sub-agent does its work; only the final result returns to the parent.

In dualpass, stage skills declare sub-agent specs in their frontmatter:

```yaml
---
name: outline-author
sub_agents:
  - name: precedent-summarizer
    prompt: "Summarize chunk-{prior}-outline-FINAL.md in 200 words..."
    tools: [read_file]
    context_cap_tokens: 8000
---
```

**Why sub-agents.** Two reasons, per Anthropic engineering:

1. **Context isolation.** The parent's context never sees the sub-agent's intermediate tool calls and reasoning — only the final summary. Keeps the parent lean.
2. **Parallelization.** Multiple sub-agents can run concurrently; the parent waits for all to return.

**When NOT to use sub-agents.** When the parent needs the sub-agent's intermediate reasoning to make its own decisions. Cognition Labs has documented this case (their "Don't Build Multi-Agents" position): if the work is deeply interdependent, splitting it across isolated contexts loses information that hurts quality.

dualpass takes no side. Sub-agents are an optional skill-declared affordance; use them when the task shape rewards isolation.

---

## 7. Planning — the first N stages of your pipeline

dualpass has no formal "planner" component. Planning happens in stages, like everything else.

The default `examples/coding-agent/` ships with a three-stage planning chain before code execution:

| Stage | Role |
|---|---|
| `research` | Expands a vague request into a research document with cited sources and decomposed subjects |
| `outline` | Produces a structured outline of work to be done, with explicit deliverables |
| `spec` | Elaborates the outline into a detailed spec with checkpoints, acceptance criteria, and constants |

The `code` stage author then implements against the spec. The `audit` and `handoff` stages close the loop.

**You can collapse or extend this chain.** A simpler workflow might be `research → execute → audit`. A more rigorous one might add `prompt` (the executable build prompt) and `independent-review` (a pre-execution sanity check). Define your pipeline in `config/stages.yaml`.

---

## 8. Sandbox / permissions — `config/permissions.yaml`

dualpass is opinionated about safety: **default to asking. Make autonomy opt-in. Make full bypass deliberately inconvenient.** This matches the consensus posture across Claude Code, Cursor, and other shipped harnesses.

```yaml
# config/permissions.yaml
default_posture: ask              # ask | auto-accept-safe | plan-only | bypass
mutating_actions_require_approval: true
opt_in_skips:
  GRACE_SKIP_PRE_CODE_INDEPENDENT_REVIEW: false
  GRACE_SKIP_ARTIFACT_PREFLIGHT: false
  GRACE_SKIP_CUMULATIVE_COUNT_CHECK: false
```

The agent's CLI flags (e.g. `--dangerously-skip-permissions`, `--yolo`) are part of the `command` template in `config/agents.yaml`, not a separate config — the harness is honest about which flags it passes.

**dualpass will never default-enable bypass mode.** You have to opt in per-environment, per-config-line. This is by design.

---

## 9. Observability — `.dualpass-state/logs/` + `dualpass status`

Every loop iteration involves an LLM call (non-deterministic, expensive, occasionally wrong). Without observability you cannot debug, attribute cost, or talk to users about what happened.

dualpass's observability surface:

- **Per-turn message logs.** `.dualpass-state/logs/<unit>-<stage>-r<round>-<iso-timestamp>-try<N>.log` — the full conversation, replayable.
- **Tool-execution logs.** Implicit in the message logs (tool calls + results are inline).
- **Structured event log.** `.dualpass-state/<unit>-events.jsonl` — one JSON object per state transition. Machine-readable for downstream tooling.
- **Cost ledger.** `.dualpass-state/<unit>-cost-summary.md` — running tally of token usage and (where the CLI reports it) dollars spent.
- **Markers.** `<unit>-build-complete.md`, `<unit>-pipeline-closed.md`, `<unit>-circuit-breaker-tripped.md`, `<unit>-stuck-<reason>.md`.
- **`dualpass status [--unit <id>]`** — human-readable rollup that reads all of the above and renders the current pipeline state.

**Build observability first, not last.** It is the boring component you regret skipping.

---

## The four dualpass-specific reliability patterns

These are reliability practices distilled from a real-world campaign of 17+ multi-day agent builds. They are not in the canonical 9-component frame — they sit on top of it.

### Pattern 1: Cross-vendor reviewer fallback

A single reviewer provider going down (rate limit, transient outage, API change) used to mean dead pipelines. dualpass wraps every reviewer launch with a fallback wrapper:

- Primary reviewer (e.g. `cursor-agent`) fails with `[resource_exhausted]` N consecutive times (default N=3).
- Harness automatically retries with the configured fallback reviewer (e.g. `claude`).
- Log records which provider served the review.
- No silent skips. The dual-pass review contract is preserved even when one vendor is sick.

### Pattern 2: Stuck-marker pattern

When a stage cannot proceed and the failure needs human judgment, dualpass writes `.dualpass-state/<unit>-stuck-<reason-kebab>.md` with diagnosis + remediation paths. The architect resolves it (often: write an override file), edits the marker's status header to `RESOLVED` *in place* (does not delete), and relaunches.

**Why update-in-place not delete:** the stuck-marker is also a historical record. Future builds benefit from grepping past stuck reasons.

### Pattern 3: Architect deviations-accepted override

When an audit verdict comes back as `PASS_WITH_DEVIATIONS` and the deviations are pre-existing co-tenant debt (not introduced by this unit), re-auditing in a loop discovers nothing new — it just burns budget. The architect resolves by writing `units/<unit-id>/audit-FINAL-deviations-accepted.md` citing the precedent. dualpass's handoff gate honors this file and proceeds.

This is a deliberate trust escalation — humans accept known issues, machines proceed.

### Pattern 4: Retrospective-as-first-class

Every unit produces a retrospective fragment at `units/<unit-id>/retro.md`. Friction patterns, new issues, recommended fixes (to a skill, gate, or controller behavior). The `dualpass retro <unit-range>` command aggregates these into a campaign retrospective at `docs/_project/RETROSPECTIVES/<range>.md`.

The retrospective is not a feel-good document — it is the input to pattern hardening. When a friction pattern repeats across units, that is the signal to write a new gate, update a skill, or patch the controller. Humans do the patching; dualpass surfaces the signal.

---

## What dualpass deliberately does NOT do

- **No self-modifying code.** Lessons are written by humans; gates are added by humans. `dualpass propose-gate` (v2) may *suggest* additions, but never applies them.
- **No DAG execution in v1.** Fixed cycles. `dualpass propose-dag` (v1) prompts you to scope a DAG if your task needs one, but you implement it outside dualpass.
- **No vendor SDK imports.** Everything is CLI-templated.
- **No bundled web UI, no SaaS layer, no plugin marketplace.** Filesystem-local. Fork it if you want hosted.

## Further reading

- [12-factor-agents](https://github.com/humanlayer/12-factor-agents) — Dex Horthy / HumanLayer. The most coherent normative framework for agent reliability.
- [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) — Anthropic. The workflows-vs-agents distinction.
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) — Anthropic. The artifacts pattern that dualpass implements.
- [Harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps) — Anthropic. Planner/Generator/Evaluator decomposition.
- [Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) — Cognition Labs. The credible counter-position on sub-agent decomposition.
- [The Lethal Trifecta](https://simonw.substack.com/p/the-lethal-trifecta-for-ai-agents) — Simon Willison. The security frame for tool-using agents.
