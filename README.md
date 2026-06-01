# dualpass

[![tests](https://github.com/Chris-Rebentisch/dualpass/actions/workflows/test.yml/badge.svg)](https://github.com/Chris-Rebentisch/dualpass/actions/workflows/test.yml)
[![license: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![python: 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
<!-- enable once published to PyPI -->
<!-- [![PyPI](https://img.shields.io/pypi/v/dualpass.svg)](https://pypi.org/project/dualpass/) -->
<!-- [![Downloads](https://static.pepy.tech/badge/dualpass/month)](https://pepy.tech/project/dualpass) -->
<!-- [![Release](https://img.shields.io/github/v/release/Chris-Rebentisch/dualpass.svg)](https://github.com/Chris-Rebentisch/dualpass/releases/latest) -->


**One agent ships the work. A different vendor ships the review.**

A reliability-first agent harness for staged agentic pipelines, with cross-vendor independent review built in as a primitive. Author with Claude. Audit with Cursor. Or swap either for whatever you trust — `dualpass` is CLI-agnostic about both.

> **Status:** v1.0.1 — feature-complete. CLI surface, controller, dual-pass parallel reviewer, cross-vendor fallback, circuit breaker, watchers, retro tooling, real context-bundle + precedent-cache builders, build-marker frontmatter parsing, 5 built-in preflight gates, cross-unit retro pattern aggregation. Tests pass across Python 3.12/3.13 on Ubuntu and macOS.

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

- **Solo devs and small teams building multi-stage coding agents** — you want what a staged pipeline gives you (research → spec → code → audit → handoff), without inheriting somebody else's domain logic.
- **Anyone running long-lived agentic workflows** — research, content production, ops runbooks. The stage chain is configurable; coding is just the default example.

## The nine components

`dualpass` is designed around the nine canonical components of a production agent harness. See [docs/CONCEPTS.md](docs/CONCEPTS.md) for the full mapping.

| Component | Where it lives |
|---|---|
| Model | `config/agents.yaml` — named agent roles → CLI invocation templates |
| Controller | `src/dualpass/controller.py` — the deterministic loop |
| Tools | Stage skills + drop-in gate plugins |
| Context window | `src/dualpass/context.py` — stage-context bundle + precedent-cache builders (compressed upstream-FINAL summaries + recent ratified precedents) |
| Memory | `.dualpass-state/<unit>/` artifacts + project docs (PROJECT/DECISIONS/BACKLOG/DOC-MAP); `memory.read_build_marker` parses build-complete YAML frontmatter |
| Sub-agents | Documented affordance in stage skills; orchestration deferred to a future release (see [ROADMAP](docs/ROADMAP.md) if present, or CHANGELOG) |
| Planning | The first N stages of your pipeline (default: research → outline → spec) |
| Sandbox / permissions | `config/permissions.yaml` — tiered, opt-in autonomy |
| Observability | `.dualpass-state/logs/` + structured event log + `dualpass status` |

## Quick start

Two install paths, pick whichever fits.

```bash
# Option A — install from PyPI (when published)
pip install dualpass
dualpass init my-project
cd my-project && dualpass run --unit demo-001 --provider mock --ignore-breakpoints

# Option B — from source
git clone https://github.com/Chris-Rebentisch/dualpass && cd dualpass
pip install -e .   # requires Python 3.12 or 3.13
dualpass init my-project
cd my-project && dualpass run --unit demo-001 --provider mock --ignore-breakpoints
```

`--provider mock` runs the full stage chain end-to-end without calling any real LLM — useful for verifying the harness works before pointing it at `claude` + `cursor-agent` for live runs.

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

## How it compares

`dualpass` picks reliability over composability — fewer ways to wire things together, more guarantees about what happens when something goes wrong. The honest comparison below is meant to surface tradeoffs, not score points; where another project genuinely wins on a dimension, that's the point of putting it in the table.

| Project | Cross-vendor reviewer | Fixed pipeline vs DAG | CLI-first | SDK lock-in | Watchers | Sandbox tiers |
|---|---|---|---|---|---|---|
| dualpass | Built-in | Fixed | Yes | None (CLI templates) | Yes (3) | Yes (4 postures) |
| LangGraph | DIY | DAG | No (Python API) | LangChain | No | DIY |
| CrewAI | DIY | Role-based | No (Python API) | CrewAI | No | DIY |
| HumanLayer 12-factor-agents | N/A (framework-agnostic spec) | N/A | N/A | N/A | N/A | N/A |
| smolagents | DIY | Single-turn | Yes | huggingface_hub | No | DIY |
| AutoGen | DIY | Multi-agent | No (Python API) | autogen | No | DIY |

If you need DAG flexibility, LangGraph beats `dualpass` on that axis. If you want a single-turn, lightweight runner over Hugging Face Hub, smolagents is closer to that shape. `dualpass` is the right pick when you want a long-running staged pipeline with cross-vendor review and recoverable failure modes by default.

## Documentation

- [CONCEPTS.md](docs/CONCEPTS.md) — the nine components + dualpass-specific reliability patterns
- [RUNBOOK.md](docs/RUNBOOK.md) — first-boot walkthrough and recovery procedures
- [CONFIG-REFERENCE.md](docs/CONFIG-REFERENCE.md) — every config file, every field
- [CHANGELOG.md](CHANGELOG.md) — release notes
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute

## License

Apache 2.0. See [LICENSE](LICENSE). Explicit patent grant included.

## Acknowledgments

`dualpass` distills patterns from published guidance by Anthropic engineering (Claude Code, "Building Effective Agents," "Effective harnesses for long-running agents," "Harness design for long-running apps"), HumanLayer's [12-factor-agents](https://github.com/humanlayer/12-factor-agents) (Dex Horthy), Cognition Labs ("Don't Build Multi-Agents"), and Simon Willison's writing on tool-use security, hardened against patterns learned running a production staged-pipeline build. Names credited where claims appear in the docs.

---

<sub>Python 3.14 + macOS editable installs (`pip install -e .`) silently fail to expose the package because Python 3.14's `site.py` skips `.pth` files that carry the auto-applied `com.apple.provenance` xattr. The published wheel works on 3.14, and the test suite works via the repo's `conftest.py`. See [CONTRIBUTING.md](CONTRIBUTING.md) for workarounds.</sub>
