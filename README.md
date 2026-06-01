# dualpass

**One agent ships the work. A different vendor ships the review.**

A reliability-first agent harness for staged agentic pipelines, with cross-vendor independent review built in as a primitive. Author with Claude. Audit with Cursor. Or swap either for whatever you trust — `dualpass` is CLI-agnostic about both.

> **Status:** v0 scaffolding. Not yet usable. Watch [CHANGELOG.md](CHANGELOG.md) for release progress.

---

## What it is

`dualpass` is the orchestration layer around a staged agentic pipeline — the deterministic code that runs the loop, manages context, owns the gates, handles permissions, and (the headline feature) wires a **different-vendor model as the independent reviewer** of every stage's output.

The pattern this productizes:

1. The **author agent** (default: `claude`) produces a stage's artifact — research, outline, spec, code, audit, whatever your stages are.
2. The **reviewer agent** (default: `cursor-agent`) — running on a *different vendor's model* — judges the output, runs live-fire probes against the claims, and either approves or sends back findings.
3. The author iterates until approved, gates run, the next stage launches.
4. If either provider fails (`resource_exhausted`, transient outage), the harness falls back to a configured second reviewer (default: `claude` again) — never silently skips review.

Why cross-vendor matters: a single agent grading its own work is the most reliable way to ship plausible-but-wrong output. Anthropic engineering documented this pattern as a fix for self-evaluation bias. `dualpass` makes the pattern a first-class feature, not a thing you have to build yourself.

## What it isn't

- **Not a visual builder.** No web UI, no DAG editor. CLI + YAML.
- **Not a replacement for LangGraph or CrewAI.** Different design point: opinionated reliability, not flexible composition.
- **Not a hosted service.** Local filesystem only. If someone wants SaaS, fork it.
- **Not a model marketplace.** Bring your own CLIs (`claude`, `cursor-agent`, `codex`, or anything matching the agent-template contract).

## Who it's for

Two audiences, one v1:

- **Solo devs and small teams building multi-stage coding agents** — you want what GrACE-style staged pipelines give you (research → spec → code → audit → handoff), without inheriting somebody else's domain logic.
- **Anyone running long-lived agentic workflows** — research, content production, ops runbooks. The stage chain is configurable; coding is just the default example.

## The nine components

`dualpass` is designed around the nine canonical components of a production agent harness. See [docs/CONCEPTS.md](docs/CONCEPTS.md) for the full mapping.

| Component | Where it lives |
|---|---|
| Model | `config/agents.yaml` — named agent roles → CLI invocation templates |
| Controller | `src/dualpass/controller.py` — the deterministic loop |
| Tools | Stage skills + drop-in gate plugins |
| Context window | `src/dualpass/context.py` — stage-context + precedent-cache builders |
| Memory | `.dualpass-state/` + project docs (PROJECT/DECISIONS/BACKLOG/DOC-MAP) |
| Sub-agents | Stage skills can declare sub-agent specs |
| Planning | The first N stages of your pipeline (default: research → outline → spec) |
| Sandbox / permissions | `config/permissions.yaml` — tiered, opt-in autonomy |
| Observability | `.dualpass-state/logs/` + structured event log + `dualpass status` |

## Quick start (planned — not yet functional)

```bash
git clone https://github.com/Chris-Rebentisch/dualpass && cd dualpass
uv sync
dualpass doctor                    # probe environment
dualpass init my-project --example coding-agent
cd my-project
dualpass run --unit demo-001 --provider mock
dualpass status --unit demo-001
```

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for the full first-boot walkthrough.

## Why dualpass

**Why a different-vendor reviewer?**
A single model grading its own work hits self-evaluation bias hard — it confidently praises mediocre output. Adversarial review by a different vendor's model (different training data, different RLHF signal, different failure modes) catches what a same-vendor reviewer misses. Anthropic's engineering team documented this as the strongest known lever against self-evaluation bias for long-running coding agents.

**Why fixed stages instead of arbitrary DAGs?**
Fixed cycles are easier to reason about, easier to checkpoint, easier to recover. The DAG flexibility of frameworks like LangGraph buys you composability at the cost of debuggability. `dualpass` picks the other tradeoff. (If your use case genuinely needs a DAG, `dualpass propose-dag` will help you scope one — but v1 ships fixed-cycle only.)

**Why ship watchers?**
Background daemons that auto-trigger downstream stages when an upstream artifact lands. The reliability lessons here (seed-before-going-live, single-flight guards, split-parent skip gates) are load-bearing — without them, a multi-day build is fragile to a single missed handoff. They're in v1 from day one.

**Why CLI-template agent invocation?**
Because the LLM ecosystem is fragmenting faster than any SDK can keep up. As long as a model has a CLI that takes a prompt and returns a string, `dualpass` can drive it. No SDK lock-in.

## Documentation

- [CONCEPTS.md](docs/CONCEPTS.md) — the nine components + dualpass-specific reliability patterns
- [RUNBOOK.md](docs/RUNBOOK.md) — first-boot walkthrough and recovery procedures
- [CONFIG-REFERENCE.md](docs/CONFIG-REFERENCE.md) — every config file, every field
- [CHANGELOG.md](CHANGELOG.md) — release notes
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute

## License

Apache 2.0. See [LICENSE](LICENSE). Explicit patent grant included.

## Acknowledgments

`dualpass` distills patterns shipped in the GrACE knowledge-graph project's pipeline automation, plus published guidance from Anthropic engineering (Claude Code, "Building Effective Agents," "Effective harnesses for long-running agents," "Harness design for long-running apps"), HumanLayer's [12-factor-agents](https://github.com/humanlayer/12-factor-agents) (Dex Horthy), Cognition Labs ("Don't Build Multi-Agents"), and Simon Willison's writing on tool-use security. Names credited where claims appear in the docs.
