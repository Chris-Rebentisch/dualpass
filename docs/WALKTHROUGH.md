# Walkthrough — a unit from init to handoff

This walkthrough takes a brand-new project from `dualpass init` to a completed unit using the mock provider. Mock output is intentional: it lets the example be deterministic and reproducible without spending tokens. The shape — author writes, reviewer judges, controller advances — is identical to a live run.

If you have not installed dualpass yet, see [the README quick-start](../README.md#quick-start) first.

---

## 1. Scaffold a project

```bash
$ dualpass init demo-pipeline
Scaffolded a new dualpass project at demo-pipeline/ from the coding-agent example.

Next steps:
  1. cd demo-pipeline
  2. dualpass doctor             # confirm agent CLIs + config are healthy
  3. Edit config/agents.yaml to point at the CLIs you actually have installed
  4. Edit skills/<stage>/SKILL.md for each stage to teach the agent your domain
  5. dualpass run --unit demo-001 --provider mock --ignore-breakpoints
```

What landed:

```
demo-pipeline/
  config/
    agents.yaml         # author/reviewer CLI templates
    dualpass.json       # project metadata
    permissions.yaml    # sandbox posture
    stages.yaml         # the 7-stage chain (or whatever you configure)
  skills/
    research/SKILL.md   research/REVIEWER.md
    outline/...         outline/REVIEWER.md
    spec/...            spec/REVIEWER.md
    prompt/...          prompt/REVIEWER.md
    code/SKILL.md       # no REVIEWER — gates handle preflight; the audit stage handles judgment
    audit/...           audit/REVIEWER.md
    handoff/...         handoff/REVIEWER.md
  docs/_project/        # PROJECT, DECISIONS, BACKLOG, DOC-MAP — your durable memory tier
```

---

## 2. Sanity check

```bash
$ cd demo-pipeline
$ dualpass doctor
dualpass doctor — environment probe
  Python: 3.13.x (>=3.12 required: OK)
  agent CLIs:
    - claude: /Users/you/.local/bin/claude
    - cursor-agent: /Users/you/.local/bin/cursor-agent
    - codex: NOT FOUND
  state dir: .dualpass-state (writable)
  config: 4 files at config/ (all valid)

doctor: OK
```

A missing agent CLI is reported but not fatal — the mock provider doesn't need them. Codex is shown as a probe target so you know what's optional.

---

## 3. Run a unit (mock provider)

```bash
$ dualpass run --unit demo-001 --provider mock --ignore-breakpoints
unit 'demo-001': completed 7 stage(s) — research, outline, spec, prompt, code, audit, handoff
```

That's the headline. Each of the 7 stages ran author + reviewer, every reviewer returned `approved`, every approval flipped a `-v1.md` artifact into a `-v1-FINAL.md`, and the controller advanced.

---

## 4. What the event log says

```bash
$ head -5 .dualpass-state/demo-001-events.jsonl
{"iso_timestamp": "...", "type": "lockfile_acquired",      "unit": "demo-001", "stage": null}
{"iso_timestamp": "...", "type": "unit_started",           "unit": "demo-001", "stage": null, "payload": {"provider": "mock", "ignore_breakpoints": true}}
{"iso_timestamp": "...", "type": "stage_round_started",    "unit": "demo-001", "stage": "research", "payload": {"round": 1, "max_rounds": 6}}
{"iso_timestamp": "...", "type": "stage_completed",        "unit": "demo-001", "stage": "research", "payload": {"verdict": "approved", "served_by": "mock"}}
{"iso_timestamp": "...", "type": "stage_finalized",        "unit": "demo-001", "stage": "research", "payload": {"final": ".../research-v1-FINAL.md"}}
```

The closed-vocabulary `type` field is the contract every downstream tool (`status`, `retro`, gates) reads.

---

## 5. What an artifact looks like

```bash
$ cat .dualpass-state/demo-001/research-artifact-v1.md
---
title: Mock research artifact
unit: demo-001
stage: research
round: 1
---

# Mock artifact — stage 'research'

- unit: `demo-001`
- round: 1
- author_skill: `skills/research/SKILL.md`
```

With a real author, the body would be substantial research with cited sources. The frontmatter is the same shape — that's what `check-frontmatter` and `check-marker-frontmatter` gates look for.

---

## 6. Why the dual-pass / cross-vendor pattern matters

Imagine a real run, not the mock. The author (Claude) writes:

```
AC1: exactly 12 tests pass.
```

A same-vendor reviewer would mostly nod — the sentence is grammatical and looks specific. The `check-acceptance-criteria-wording` gate catches the brittleness first (the count creates a treadmill — adding one test breaks the criterion), but if the gate were absent, a different-vendor reviewer with different RLHF priors is the next line of defense. Cursor's reviewer, asked to verify the same line, is much more likely to push back:

```
Reviewer verdict: rejected
Severity: design

The "exactly 12" wording will require the criterion to be updated every time
a test is added or removed. Suggest ">= 12 tests pass" or "all tests under
tests/foo/ pass" — both express the intent without coupling to the count.
```

That's the load-bearing claim of dualpass in one fragment: a different-vendor reviewer catches the kind of plausible-but-wrong output that same-vendor self-evaluation tends to wave through.

When you swap the mock provider for `--provider live`, this exact mechanic runs against your CLIs.

---

## 7. Status and retro

```bash
$ dualpass status --unit demo-001
                             dualpass — unit status
┏━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ Unit     ┃ State     ┃ Stage   ┃ Completed         ┃ Last event       ┃ Lock ┃
┡━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ demo-001 │ completed │ handoff │ research, outline,│ lockfile_released│  -   │
│          │           │         │ spec, prompt,     │ @ 2026-06-01T... │      │
│          │           │         │ code, audit,      │                  │      │
│          │           │         │ handoff           │                  │      │
└──────────┴───────────┴─────────┴───────────────────┴──────────────────┴──────┘
```

```bash
$ dualpass retro --unit demo-001
Wrote docs/_project/RETROSPECTIVES/demo-001.md
```

The retro template is pre-populated with the unit's run summary. Fill in **What went wrong**, **Changes for next time**, and **Friction patterns** in markdown — those sections are what `dualpass retro --range 001..010` later scans across all your units to surface recurring patterns (with EventType counts and bigram frequencies).

---

## 8. Next steps

- Swap the mock provider for `live`: edit `config/agents.yaml` to point at the CLIs you have. Replace one of the author or reviewer with a different vendor — that's the whole point.
- Customize a skill: pick `skills/spec/SKILL.md`, write the kind of spec you actually want, run the unit again. Iterate on the skill until the LLM produces what you'd produce by hand.
- Add a preflight gate to a stage in `config/stages.yaml`. The 5 built-in gates ship registered; declare any subset of `check-frontmatter`, `check-line-citations`, `check-single-flight`, `check-marker-frontmatter`, `check-acceptance-criteria-wording`.
- Set up a watcher to auto-advance units past stage breakpoints overnight: `dualpass watcher start research`.

Read [CONCEPTS.md](CONCEPTS.md) for what each piece is doing under the hood; [RUNBOOK.md](RUNBOOK.md) for the recovery patterns when things go sideways.
