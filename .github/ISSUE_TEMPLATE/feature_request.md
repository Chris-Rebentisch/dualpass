---
name: Feature request
about: Suggest a change or addition to dualpass
title: ""
labels: enhancement
assignees: ""
---

## What problem does this solve?

The user-facing problem. Be specific about who experiences it and when.

## Proposed solution

What you'd like to see. Code sketches welcome but not required.

## Alternatives considered

Other approaches you thought about and why you'd pick this one.

## Surface area

Would this require:

- [ ] A new built-in gate (`src/dualpass/gates/builtins.py`)
- [ ] A new `EventType` entry (observability contract change)
- [ ] A new config field (schema + loader changes)
- [ ] A new CLI subcommand
- [ ] A new stage primitive
- [ ] Documentation only

## Out-of-scope check

dualpass intentionally declines: vendor-SDK integration, DAG execution engines, hosted/SaaS layers, web UIs. If this proposal touches any of those, briefly say why it should be reconsidered.
