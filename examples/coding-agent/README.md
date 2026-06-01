# Example: coding-agent

A worked example of dualpass configured for a 7-stage coding pipeline. This is the structure that `dualpass init <path> --example coding-agent` scaffolds for you.

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
