# Skills — coding-agent example

One directory per stage. Each directory contains:

- `SKILL.md` — the author agent's instructions (Anthropic skill format: YAML frontmatter + markdown body)
- `REVIEWER.md` — the reviewer agent's instructions (same format, different perspective)
- `references/` (optional) — additional in-context material the agent can read

## Skill structure (Anthropic format)

```markdown
---
name: <stage>-author
description: One-sentence description of what this skill produces.
sub_agents: []                  # optional — declare sub-agents here
context_sources:                # files the agent should read first
  - units/{unit_id}/<predecessor>-FINAL.md
  - .dualpass-state/{unit_id}-stage-context.md
artifacts_produced:
  - units/{unit_id}/<stage>-v{round}.md
success_criteria:
  - [Mechanical or semantic check #1]
  - [Mechanical or semantic check #2]
---

# <Stage>-author skill

[Free-form instructions for the agent. The agent reads this verbatim.]

## Process

1. ...
2. ...

## Constraints

- ...
```

## Adapting these skills

The shipped skills are deliberately generic. To make them yours:

1. Open `<stage>/SKILL.md` in Claude (or your model).
2. Tell it: *"Adjust this skill for {my project type, my conventions, my stack}."*
3. Save the output back to `<stage>/SKILL.md`.
4. Repeat for `<stage>/REVIEWER.md`.

The skills become better with use — when retros (`dualpass retro`) surface friction patterns, update the relevant skill. That's the learnability loop.

## Skills shipped in this example

| Stage | Skill | Reviewer |
|---|---|---|
| `research` | research/SKILL.md | research/REVIEWER.md |
| `outline` | outline/SKILL.md | outline/REVIEWER.md |
| `spec` | spec/SKILL.md | spec/REVIEWER.md |
| `prompt` | prompt/SKILL.md | prompt/REVIEWER.md |
| `code` | code/SKILL.md | (uses dedicated audit stage instead of a per-stage reviewer) |
| `audit` | audit/SKILL.md | audit/REVIEWER.md |
| `handoff` | handoff/SKILL.md | handoff/REVIEWER.md |
