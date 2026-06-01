# Example: coding-agent

A worked example of dualpass configured for a 7-stage coding pipeline. This is the structure that `dualpass init <path> --example coding-agent` scaffolds for you.

## The cycle

```
research then outline then spec then prompt then code then audit then handoff

   v each stage:
   [author CLI] then artifact-vN.md
                   v
   [reviewer CLI (different vendor)] then verdict
                   v
   if approved: advance     if rejected: revision-vN+1
                   v
   [optional] preflight gates run before each reviewer launch
```

Every stage produces a versioned artifact at `.dualpass-state/<unit>/<stage>-vN.md`. When a reviewer approves, the controller copies the artifact to `<stage>-vN-FINAL.md` and the next stage begins. When a reviewer rejects, the author runs again as `vN+1`, reading the previous artifact plus the reviewer's findings.

The split-vendor pattern matters: when Claude authors and Cursor reviews (or vice versa), the reviewer doesn't share the author's training data or RLHF signal, so it catches failure modes a same-vendor reviewer rubber-stamps.

## What's in this example

`config/` holds the four config files dualpass reads at startup (`dualpass.json` for project metadata, `agents.yaml` for author/reviewer CLI commands, `stages.yaml` for the stage list and preflight-gate wiring, `permissions.yaml` for tool/path allowlists). `skills/<stage>/SKILL.md` is the author instructions for that stage; `skills/<stage>/REVIEWER.md` is the reviewer instructions.

- `config/dualpass.json` — project name, root paths
- `config/agents.yaml` — author and reviewer CLI templates
- `config/stages.yaml` — the 7 stages and which preflight gates run before each
- `config/permissions.yaml` — tool/path allowlists for both roles
- `skills/research/`, `skills/outline/`, `skills/spec/`, `skills/prompt/`, `skills/code/`, `skills/audit/`, `skills/handoff/` — one SKILL.md per stage, plus REVIEWER.md for every stage that has a reviewer (code does not; the audit stage is its review surface)
- `skills/README.md` — explains the skill format and how to adapt

## The 7-stage chain

| # | Stage | Author produces | Reviewer judges | Notes |
|---|---|---|---|---|
| 1 | `research` | Research document with cited sources, decomposed subjects | Coverage + source quality | First planning stage |
| 2 | `outline` | Structured outline of work to be done | Linkage to research, completeness | Second planning stage |
| 3 | `spec` | Detailed build spec with checkpoints + AC | Spec-vs-outline traceability, AC quality | Third planning stage (dual-pass reviewer) |
| 4 | `prompt` | Executable build prompt mapping spec → CPs | 1-to-1 checkpoint mapping, no drift | Pre-execution sanity |
| 5 | `code` | Working code that satisfies the spec | Independent audit against AC | Where the work actually happens |
| 6 | `audit` | Verdict (PASS / PASS_WITH_DEVIATIONS / FAIL) + findings | Audit faithfulness | Gates handoff |
| 7 | `handoff` | Session handoff doc reconciling spec ↔ delivered | Linkage completeness | Last stage; flips backlog status |

## Cloning the pattern

Fork this directory as the starting point for any new dualpass project. `dualpass init <new-path> --example coding-agent` does the scaffolding for you: it copies the `config/` and `skills/` trees into `<new-path>` and rewrites `config/dualpass.json` with the new project name. From there, edit `config/stages.yaml` to drop or add stages, then walk each `skills/<stage>/SKILL.md` and adapt it to your project's conventions.

## Tailoring

You almost certainly do NOT want all 7 stages for every project. Edit `config/stages.yaml` to drop or add stages. Common simpler chains:

- **3-stage:** `research → execute → audit`
- **4-stage:** `outline → spec → code → audit`
- **5-stage:** `research → outline → code → audit → handoff`

## Adapting the skills

Each `skills/<stage>/` directory contains a `SKILL.md` written generically. Drop a SKILL.md into Claude (or whichever model you favor) and ask:

> Adjust this skill for {my project type, my stack, my conventions}.

The output is your project-specific skill. Save it back into the same path. dualpass loads it on the next run.

## Where the cross-vendor review happens

Look at `config/agents.yaml`. The `author` role defaults to Claude; the `reviewer` role defaults to Cursor's `cursor-agent`. Every stage's output goes through this split — when Claude is the author, Cursor judges its work; that's the dual-pass cycle.

If you don't have one of those CLIs installed, swap the `command` template in `config/agents.yaml` to whatever you do have. The contract is documented in `docs/CONFIG-REFERENCE.md`.

## Mock provider

Use `--provider mock` to validate your config + skill structure without spending money. The mock provider returns canned responses; a full 7-stage mock run completes in under a minute.
