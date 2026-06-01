# RUNBOOK

First-boot walkthrough and recovery procedures. Read [CONCEPTS.md](CONCEPTS.md) first if you have not.

> **Status note:** dualpass is at v0.1.0a0 (pre-alpha). The commands documented below are the v1 target. Where a command is not yet implemented, this runbook says so.

---

## Part 1 — First boot (target experience, not yet functional)

### Prerequisites

- **Python 3.12 or 3.13** (`python3 --version`).
- **uv** for package management — `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **At least one author-capable CLI** installed and on your `PATH`:
  - `claude` — install via the [Claude Code installer](https://claude.com/claude-code).
  - or `codex`, or any other CLI matching the agent-template contract (see [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md)).
- **At least one reviewer-capable CLI** installed:
  - `cursor-agent` — install via [Cursor](https://cursor.com).
  - or use `claude` for both (single-vendor; loses the cross-vendor benefit but works).

### Five-minute smoke test

```bash
git clone https://github.com/Chris-Rebentisch/dualpass && cd dualpass
uv sync                                        # install deps
uv run dualpass --version                      # should print 0.1.0a0
uv run dualpass --help                         # should print top-level commands
```

If those four commands all succeed, the package is installed correctly. Continue to environment probe.

### Environment probe

```bash
uv run dualpass doctor
```

Reports on:

- Python version
- `uv` version
- Which agent CLIs are on `PATH` (`claude`, `cursor-agent`, `codex`, etc.)
- Which CLIs respond to `--version`
- Whether a writeable `~/.dualpass/` exists
- Whether the working directory has any `.dualpass-state/` lockfiles that would block a new run

A clean run returns exit 0 with a green table. A degraded run returns exit 1 with red entries pointing at the fix. The probe is non-destructive.

### Scaffold a new project

```bash
uv run dualpass init my-project --example coding-agent
cd my-project
```

This copies the `examples/coding-agent/` skeleton into `my-project/`:

```
my-project/
├── config/
│   ├── agents.yaml          # cross-vendor reviewer config
│   ├── stages.yaml          # 7-stage coding pipeline
│   ├── permissions.yaml     # opt-in autonomy
│   └── dualpass.json        # top-level harness config
├── docs/
│   └── _project/
│       ├── PROJECT.md       # what this project is
│       ├── DECISIONS.md     # decision registry
│       ├── BACKLOG.md       # work units status
│       ├── DOC-MAP.md       # canonical doc index
│       └── RUNBOOK.md       # project-specific operational notes
├── skills/                  # the 7 stage skills, generic versions
└── units/                   # empty — your work units live here
```

Edit `docs/_project/PROJECT.md` to describe what you're actually building. The other project docs can stay as templates until you have specific decisions to record.

### First unit (mock provider)

```bash
uv run dualpass run --unit demo-001 --provider mock
```

The mock provider returns canned responses for every author and reviewer call. No real LLM is invoked, no cost is incurred. Use this to confirm your pipeline structure is sound before any real run.

Expected output: a run that completes all 7 stages in under a minute, leaving `units/demo-001/*-FINAL.md` artifacts and a `.dualpass-state/demo-001-pipeline-closed.md` marker.

### First unit (live provider — costs real money)

```bash
uv run dualpass run --unit my-001 --provider live --from-stage research
```

This launches the real pipeline. The orchestrator log goes to `.dualpass-state/logs/my-001-<stage>-<round>-<timestamp>.log`. Per-turn message logs are in the same directory.

Approximate cost on a small unit (Claude Opus author + Cursor reviewer): $1–5. Larger units (complex specs, long code stages) can reach $20–50.

### Check status

```bash
uv run dualpass status --unit my-001
uv run dualpass status                     # all in-flight units
uv run dualpass status --json              # machine-readable
```

Reads `.dualpass-state/` markers + logs and prints current state per unit.

---

## Part 2 — Recovery procedures

dualpass is built to fail loudly and recover deterministically. The recovery patterns below cover the common failure modes.

### A — Orchestrator killed mid-stage

**Symptom:** the run process exited (session disconnect, manual kill, OS crash) but artifacts on disk show partial progress.

**Recovery:**

1. Check `.dualpass-state/<unit>-pipeline.lock.json` — does it still exist?
2. If yes and no orchestrator PID is alive (`ps -p <pid_from_lock>`), the lock is stale. Remove it: `rm .dualpass-state/<unit>-pipeline.lock.json`.
3. Determine which stage was in flight from the last log file or `dualpass status --unit <id>`.
4. Relaunch from the *next* incomplete stage:
   ```bash
   uv run dualpass run --unit <id> --from-stage <next-stage>
   ```
   Do NOT relaunch from a stage whose `<stage>-FINAL.md` already exists — that wastes a full re-run.

### B — Audit returns `PASS_WITH_DEVIATIONS` (real code issues)

**Symptom:** the audit reviewer flagged real gaps in the code stage's output.

**Recovery:**

- Let the audit-reviewer auto-remediate if possible (the configured reviewer template has remediation authority for non-catastrophic findings).
- If auto-remediation cannot fix it, the orchestrator will halt the audit loop. Read the audit FINAL, fix manually OR relaunch the code stage with the audit findings in scope:
  ```bash
  uv run dualpass run --unit <id> --from-stage code
  ```

### C — Audit returns `PASS_WITH_DEVIATIONS` (pre-existing co-tenant debt)

**Symptom:** the audit reviewer flagged real issues — but they predate this unit and have precedent for acceptance from earlier units.

**Recovery (architect override path):**

1. Verify the findings are pre-existing by checking earlier units' audit FINALs.
2. Write `units/<unit-id>/audit-FINAL-deviations-accepted.md` citing the precedent units explicitly.
3. Relaunch from handoff: `uv run dualpass run --unit <id> --from-stage handoff`.
4. The handoff gate will read the override file and proceed.

This is a deliberate human-in-the-loop pattern. Use sparingly; over-use erodes the value of the audit.

### D — Reviewer provider exhausted (`[resource_exhausted]`)

**Symptom:** the primary reviewer CLI returns API-exhaustion errors repeatedly.

**Recovery:**

- dualpass should automatically fall back to `reviewer_fallback` (configured in `config/agents.yaml`) after N consecutive exhaustion failures (default N=3).
- If it doesn't, the fallback config is missing or misconfigured. Check `config/agents.yaml`.
- As a last resort, swap the primary reviewer command temporarily and relaunch:
  ```bash
  # Edit config/agents.yaml, point reviewer at the working CLI, then:
  uv run dualpass run --unit <id> --from-stage <current-stage>
  ```

### E — Circuit breaker tripped

**Symptom:** the controller halted after 3 no-progress auto-relaunches. A `.dualpass-state/<unit>-circuit-breaker-tripped.md` file exists.

**Recovery:**

1. Read the trip diagnostic. It will name the stage and the hash of the no-progress signal.
2. Inspect the artifact and logs — what is the agent actually doing? Common causes: spec defect, missing tool, environment broken.
3. Fix the root cause.
4. Reset the breaker: `rm .dualpass-state/<unit>-circuit-state.json`.
5. Relaunch.

### F — Watcher fired a rogue run

**Symptom:** a watcher (research → outline, prompt → code, handoff → close) triggered a build you didn't want — typically after editing a research file while the watcher was live.

**Recovery:**

1. Find the rogue process: `ps -ax | grep run_pipeline`.
2. Kill it (`kill <pid>`) and its child agent tree.
3. Remove the stale lockfile: `rm .dualpass-state/<unit>-pipeline.lock.json`.
4. Stop the offending watcher: `uv run dualpass watcher stop <name>`.
5. (v1 default) The watcher should not have fired if a build was already in flight — the `_pipeline_lock_present` guard catches this. If it did fire, file a bug.

**Prevention:** always stop watchers before editing research files or initiating a manual build. `uv run dualpass watcher status` lists current state.

### G — Stuck and no automated recovery applies

**Symptom:** you cannot identify a clean recovery path.

**Recovery:**

1. Write a stuck marker: `.dualpass-state/<unit>-stuck-<reason-kebab-case>.md` — diagnosis + remediation paths attempted.
2. Skip the unit and continue with later units (if your project supports skip-and-continue per its stage-dependency graph).
3. Return to the stuck unit later; update the marker's status header to `RESOLVED` *in place* when you fix it (do not delete — the marker is also a historical record).

---

## Part 3 — Retrospectives (the learnability loop)

After every unit closes:

```bash
uv run dualpass retro --unit <id>
```

This opens an editor with a template at `units/<id>/retro.md` (filled with auto-detected friction signals from the unit's logs and markers). You add:

- What went well
- What friction patterns emerged
- New issues that should be standardized as fixes (to a skill, gate, or controller behavior)
- Open questions

After a range of units, aggregate:

```bash
uv run dualpass retro --range 001..010 --output docs/_project/RETROSPECTIVES/units-001-010.md
```

This produces a campaign retrospective summarizing the unit-level retros. The output is a markdown file your team can review.

**The retrospective is the input to pattern hardening.** When a friction pattern recurs, that's the signal to write a new gate, update a skill, or patch the controller. dualpass surfaces the signal; humans do the patching.

---

## Part 4 — Asking for help

- **GitHub Issues:** https://github.com/Chris-Rebentisch/dualpass/issues — bug reports, feature requests
- **Discussions:** https://github.com/Chris-Rebentisch/dualpass/discussions — usage questions, pattern sharing
- **Security:** see [SECURITY.md](../SECURITY.md) (v1 — not yet present)

When filing an issue, include:

- dualpass version (`dualpass --version`)
- `dualpass doctor` output
- The contents of any `.dualpass-state/<unit>-stuck-*.md` or `<unit>-circuit-breaker-tripped.md`
- The last orchestrator log file
