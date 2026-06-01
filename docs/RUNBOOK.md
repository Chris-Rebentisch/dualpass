# RUNBOOK

First-boot walkthrough and recovery procedures. Read [CONCEPTS.md](CONCEPTS.md) first if you have not.

> **Status:** v1.0.0. Every command in this runbook is functional. If a command surprises you, file an issue.

---

## Fresh-install verification (30 seconds)

The fastest way to confirm dualpass is wired up and the mock pipeline runs end-to-end on your machine:

```bash
pip install dualpass        # or, from a checkout: pip install -e .
dualpass doctor
dualpass init test-pipeline
cd test-pipeline
dualpass run --unit demo-001 --provider mock --ignore-breakpoints
dualpass status --unit demo-001
```

A healthy run finishes in under a second and ends with `dualpass status` reporting `completed` for `demo-001`. If `doctor` exits non-zero, fix what it points at before continuing. If the mock run fails, the issue is in the harness wiring — not in your agent CLIs — and is worth filing.

---

## Part 1 — First boot

### Prerequisites

- **Python 3.12 or 3.13** (`python3 --version`).
- **pip** (ships with Python) — or `uv` / `pipx` if you prefer.
- **At least one author-capable CLI** installed and on your `PATH`:
  - `claude` — install via the [Claude Code installer](https://claude.com/claude-code).
  - or `codex`, or any other CLI matching the agent-template contract (see [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md)).
- **At least one reviewer-capable CLI** installed:
  - `cursor-agent` — install via [Cursor](https://cursor.com).
  - or use `claude` for both (single-vendor; loses the cross-vendor benefit but works).

The mock provider works without any of the above — it's how you smoke-test the harness before installing real CLIs.

### Two-minute smoke test

```bash
git clone https://github.com/Chris-Rebentisch/dualpass && cd dualpass
pip install -e .
dualpass --version                            # prints "dualpass 1.0.0"
dualpass --help                               # lists every command
```

### Environment probe

```bash
dualpass doctor
```

Reports on:

- Python version
- Resolved project root
- Which agent CLIs are on `PATH` (`claude`, `cursor-agent`, `codex`)
- Whether `.dualpass-state/` is writable
- Whether the project's config files are valid

Exit 0 = healthy. Exit 1 = at least one check failed (output points at the fix). The probe is non-destructive.

### Scaffold a new project

```bash
dualpass init my-project
cd my-project
```

This copies the `coding-agent` example into `my-project/`. The layout you get:

```
my-project/
├── config/
│   ├── dualpass.json        # top-level harness config (project name, breakpoints, breaker)
│   ├── agents.yaml          # agent role → CLI invocation template (author / reviewer / fallback)
│   ├── stages.yaml          # 7-stage pipeline definition
│   └── permissions.yaml     # tiered safety posture
└── skills/                  # 7 stage skills (SKILL.md + REVIEWER.md per stage)
```

After `init`, `dualpass doctor --project my-project` verifies the new project's configs validate.

### First unit (mock provider — free, offline)

```bash
dualpass run --unit demo-001 --provider mock --ignore-breakpoints
```

The mock provider returns canned author/reviewer responses. No real LLM is invoked, no cost is incurred. The run completes all 7 stages in under a second, leaving artifacts under `.dualpass-state/demo-001/<stage>-{artifact,review}-v<round>.md`.

Use the mock to verify your pipeline structure before any live run.

### First unit (live provider — costs real money)

```bash
dualpass run --unit my-001 --provider live --ignore-breakpoints
```

This calls the CLIs configured in `config/agents.yaml` (default: `claude` author + `cursor-agent` reviewer + `claude` fallback). Approximate cost on a small unit: $1–5. Larger units (long specs, complex code stages) can reach $20–50.

If you remove `--ignore-breakpoints`, the run pauses cleanly at the `code` stage (the bundled example has `breakpoints.code: true`). Resume after review with:

```bash
dualpass run --unit my-001 --provider live --from-stage code --ignore-breakpoints
```

### Check status

```bash
dualpass status                               # all units
dualpass status --unit my-001                 # single unit
dualpass status --json                        # machine-readable
```

State classes you'll see: `in-flight`, `completed`, `paused-at-breakpoint`, `blocked`, `circuit-tripped`, `stale-lock`, `unknown`.

---

## Troubleshooting / FAQ

Quick answers to the friction patterns that come up most often. Most of them are environment or configuration mistakes, not harness bugs — start here before opening an issue.

**Q. `dualpass status --unit X` says `state=unknown`.**

Probable cause: you ran the command from outside a project directory, so dualpass cannot find a `.dualpass-state/` for the unit. Either `cd` into the directory holding `config/`, or pass `--project-root <path>` to point at it explicitly.

**Q. `dualpass doctor` says everything is OK, but my pipeline still fails on the first live run.**

`doctor` checks Python, project root, CLI availability on `PATH`, state-dir writability, and config validity. It does NOT probe whether the `command` template in your `config/agents.yaml` actually invokes a working model — a typo in the CLI template will pass `doctor` and fail at runtime. Always run a mock pass first to verify pipeline structure:

```bash
dualpass run --unit <id> --provider mock --ignore-breakpoints
```

Then re-run with `--provider live`.

**Q. `pip install -e .` fails on Python 3.14.**

The package requires `>=3.12,<3.14` until the upstream fix lands. Use Python 3.12 or 3.13 for editable installs (`pip install -e .`). The wheel install (`pip install dualpass`) works on 3.14.

**Q. How do I know if a stage's preflight gates are actually running?**

Set the stage's `preflight_gates` list in `config/stages.yaml`. Each gate failure (and each pass) is recorded as a `gate_failed` / `gate_passed` event in `.dualpass-state/<unit>-events.jsonl`. An empty `preflight_gates` list means no preflight runs for that stage — silent skip, not silent failure.

**Q. The author signaled `exit_signal: stop` — what now?**

The controller wrote `.dualpass-state/<unit>-stuck-author-stop.md` capturing the author's stated reason. Read it, resolve the underlying issue (edit a skill, fix the environment, update inputs), then either:

- Delete the stuck-marker and re-run: `rm .dualpass-state/<unit>-stuck-author-stop.md && dualpass run --unit <id> --from-stage <stage>`
- Or follow the standard stuck-marker pattern: edit the header `status:` line in the stuck-marker to `RESOLVED` in place, then re-launch from the same stage.

**Q. My reviewer keeps approving things that look obviously wrong.**

Self-evaluation bias — when the same model author and review, the reviewer rubber-stamps. Check `config/agents.yaml`: the `author` role and the `reviewer` role should resolve to DIFFERENT vendors (e.g. `claude` author + `cursor-agent` reviewer, not `claude` for both). The cross-vendor split is load-bearing; the single-vendor setup is documented as a fallback but loses the disagreement signal.

---

## Part 2 — Recovery procedures

dualpass is built to fail loudly and recover deterministically. The recovery patterns below cover the common failure modes.

### A — Run process killed mid-stage

**Symptom:** the `dualpass run` process exited (Ctrl-C, OS crash, terminal disconnect) and `dualpass status` shows the unit as `stale-lock`.

**Recovery:**

1. Confirm the lock is stale: `dualpass status --unit <id>` reports `stale-lock`.
2. Remove the lockfile: `rm .dualpass-state/<id>-pipeline.lock.json`.
3. Determine which stage was in flight by reading `.dualpass-state/<id>-events.jsonl` (the last `stage_round_started` event names it).
4. Relaunch from that stage:
   ```bash
   dualpass run --unit <id> --from-stage <stage-name>
   ```

### B — Reviewer keeps rejecting (max rounds exhausted)

**Symptom:** `dualpass run` exited 1 with "blocked after N round(s)" on stderr. `dualpass status` shows `blocked`.

**Recovery:**

- Inspect the artifact + review pair under `.dualpass-state/<id>/<stage>-{artifact,review}-v<round>.md`.
- If the reviewer is being unreasonable, edit the reviewer skill (`skills/<stage>/REVIEWER.md`) to clarify the bar.
- If the author lacks context, edit the author skill (`skills/<stage>/SKILL.md`).
- Re-run from that stage:
   ```bash
   dualpass run --unit <id> --from-stage <stage-name>
   ```

### C — Reviewer provider exhausted (`[resource_exhausted]`)

**Symptom:** the primary reviewer CLI returns API-exhaustion errors repeatedly.

**Recovery:**

- The live provider automatically falls back to `reviewer_fallback` (configured in `config/agents.yaml`) after N consecutive matches against `exhaustion_patterns`. Default N=3, set via `activate_after_consecutive_exhausted`.
- If the fallback isn't kicking in, verify `config/agents.yaml` has a `reviewer_fallback` role with `exhaustion_patterns` and `activate_after_consecutive_exhausted` set.
- As a last resort, swap the primary reviewer's `command` to point at a different CLI and relaunch.

### D — Circuit breaker tripped

**Symptom:** `dualpass run` halted with "blocked after N round(s): author produced identical artifact for N consecutive rounds while reviewer kept rejecting". `.dualpass-state/<id>-circuit-tripped.md` exists. `dualpass status` shows `circuit-tripped`.

**Recovery:**

1. Read the trip diagnostic at `.dualpass-state/<id>-circuit-tripped.md` — it names the stage, rounds used, artifact hash, and the artifact + review paths.
2. Inspect the artifact and review files referenced in the diagnostic. What's the agent stuck on? Common causes: spec defect, missing tool, environment broken, reviewer asking for the impossible.
3. Fix the root cause (edit a skill, fix the environment, etc.).
4. Re-run from the tripped stage:
   ```bash
   dualpass run --unit <id> --from-stage <stage-name>
   ```
   The trip-marker is informational only — there's no separate reset step.

To tune the trip threshold, edit `circuit_breaker.max_no_progress_relaunches` in `config/dualpass.json`. Set to `1` for fastest detection; `5` for tolerance of normal author drift.

### E — Watcher fired a rogue run

**Symptom:** a watcher (`research` / `prompt` / `handoff`) triggered a `dualpass run` you didn't want — typically because an approval marker was dropped while the watcher was live.

**Recovery:**

1. Find the rogue process: `ps -ax | grep "dualpass run"`.
2. Kill it: `kill <pid>` (and any child agent CLI processes it spawned).
3. Remove the stale lockfile: `rm .dualpass-state/<id>-pipeline.lock.json`.
4. Stop the watcher: `dualpass watcher stop <name>`.
5. If the watcher fired despite the unit being locked, that's a bug — file an issue with the contents of `.dualpass-state/watcher-<name>.log`.

**Prevention:**

- The watcher checks for `<id>-pipeline.lock.json` before triggering; if a lock is held, it skips.
- Approval markers are one-shot: when the watcher acts on `<id>-approved-<stage>.md`, it writes `<id>-handled-<stage>.md` so the next poll doesn't re-trigger.
- `dualpass watcher status` reports current watcher state. Stop watchers before editing approval markers manually.

### F — Lock acquired, run never started

**Symptom:** `dualpass run` exited with "lock already held" but `dualpass status --unit <id>` reports `stale-lock`.

**Recovery:**

Remove the lock and retry. dualpass uses `O_CREAT | O_EXCL` for atomic lock creation, so this only happens when a previous run was killed before the `finally` cleanup landed.

```bash
rm .dualpass-state/<id>-pipeline.lock.json
dualpass run --unit <id>
```

### G — Stuck and no automated recovery applies

**Symptom:** you cannot identify a clean recovery path.

**Recovery:**

1. Run `dualpass retro --unit <id>` — the template includes an "At-a-glance" section seeded from the unit's events. Use it to capture what you tried.
2. File an issue with the retro contents + `.dualpass-state/<id>-events.jsonl` attached.
3. If your project has a stage chain that supports skip-and-continue, move to the next unit and revisit this one later.

---

## Part 3 — Retrospectives

After every unit closes:

```bash
dualpass retro --unit <id>
```

This creates `docs/_project/RETROSPECTIVES/<id>.md` with a template pre-populated from the unit's event log (final state, stages completed, paused-at-breakpoint, blocked-at). You fill in:

- What went well
- What went wrong
- Surprises
- Changes for next time

After a range of units, aggregate:

```bash
dualpass retro --range 001..010 --output docs/_project/RETROSPECTIVES/units-001-010.md
```

The range parser handles zero-padded numeric ranges with or without prefixes (`001..010` or `my-001..my-010`). Missing units are flagged in the rollup's frontmatter.

**The retrospective is the input to pattern hardening.** When a friction pattern recurs, that's the signal to edit a stage skill, tighten a gate, or adjust a config knob. dualpass surfaces the signal; you do the patching.

---

## Part 4 — Background watchers

For long-running pipelines, the watcher daemons auto-resume paused units when approval markers appear.

```bash
dualpass watcher start research              # daemonize the research-watcher
dualpass watcher start all                   # all three (research, prompt, handoff)
dualpass watcher status                      # check running / stopped / stale-pid
dualpass watcher stop research               # SIGTERM the watcher
```

How a watcher resumes a paused unit:

1. The user (or another process) writes `.dualpass-state/<id>-approved-<stage>.md`.
2. Within ~5 seconds (configurable via `--poll-interval`), the matching watcher notices, checks the unit isn't already locked, writes `.dualpass-state/<id>-handled-<stage>.md` to prevent double-trigger, then spawns `dualpass run --unit <id> --from-stage <stage> --ignore-breakpoints`.

On first start, the watcher seeds existing approval markers as "already handled" so it doesn't stampede the historical backlog. The seed report lives at `.dualpass-state/watcher-<name>.seed.json`.

For debugging, start in foreground: `dualpass watcher start research --foreground`.

---

## Part 5 — Asking for help

- **GitHub Issues:** https://github.com/Chris-Rebentisch/dualpass/issues — bug reports, feature requests
- **Discussions:** https://github.com/Chris-Rebentisch/dualpass/discussions — usage questions, pattern sharing
- **Security:** see [SECURITY.md](../SECURITY.md)

When filing an issue, include:

- `dualpass --version`
- `dualpass doctor` output
- The contents of any `.dualpass-state/<id>-circuit-tripped.md` or `<id>-stuck-*.md`
- The last 50 lines of `.dualpass-state/<id>-events.jsonl`
