# Changelog

All notable changes to dualpass are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Possible future work (open to feedback):

- Hosted variant (out of scope for v1 by design — currently local-filesystem only)
- PyPI publish workflow + signed releases
- Per-reviewer focus prompts for dual-pass (currently both reviewers see the same prompt; the contrast comes from LLM nondeterminism and cross-vendor fallback)
- Sub-agent orchestration (currently documented affordance only — CLI agents handle their own isolation)
- Built-in cost ledger (parsing per-CLI cost output was too vendor-specific to ship in v1; ROADMAP candidate)
- PWD halt-and-remediate and cumulative-count cascade as enforced built-in gates (currently inherited at the doctrine level only)

## [1.0.1] — 2026-06-01

A reliability-and-completeness pass over the v1.0.0 surface. Most of what landed here closes gaps where the docs described behavior that the controller did not actually implement.

### Added

- `src/dualpass/context.py` — stage-context bundle + precedent-cache builders (were `NotImplementedError` stubs in 1.0.0). The bundle compresses upstream-FINAL summaries + recent ratified precedents into a single context blob that the controller regenerates at every stage entry, so author and reviewer subprocesses start from the same compressed view of project state.
- `src/dualpass/memory.read_build_marker` — parses the build-complete marker YAML frontmatter. The controller now honors `exit_signal: stop | continue | escalate` from author output, implementing the author-driven halt contract the docs already described (lesson inherited from a production source pipeline).
- `src/dualpass/gates/` — gate registry + 5 built-in gates: `check-frontmatter`, `check-line-citations`, `check-single-flight`, `check-marker-frontmatter`, `check-acceptance-criteria-wording`.
- Controller invokes preflight gates before reviewer launch; gate failures auto-revise the round rather than advancing to review.
- Config-load validation: stages referencing unregistered gate names fail `dualpass config validate` and `dualpass doctor`. Catches typos before they cost a stage round.
- `auto_lock_finals` now actually copies approved artifacts to `<unit>/<stage>-v<N>-FINAL.md`. Previously documented, not wired.
- `dualpass retro --range` now surfaces cross-unit patterns (EventType counts + retro keyword frequencies) above the per-unit table of contents.
- New EventTypes: `gate_failed`, `stage_finalized`.

### Changed

- `requires-python` set to `>=3.12` (no upper bound). Wheel install works on Python 3.14; editable installs on 3.14 + macOS are affected by Python 3.14's `site.py` skipping `.pth` files that carry the auto-applied `com.apple.provenance` xattr. A top-level `conftest.py` adds `src/` to `sys.path` so the test suite works regardless of install state. See CONTRIBUTING.md for workarounds.
- Renamed gate `check-ac1-wording` to `check-acceptance-criteria-wording` in the example config — self-explanatory to a first-time reader, no behavioral change.
- Path layout in docs aligned to implementation: artifacts land under `.dualpass-state/<unit>/<stage>-v<N>.md`, not `units/<unit>/`. The implementation has always written to `.dualpass-state/<unit>/`; the docs were stale.

### Fixed

- CONCEPTS.md self-referential "foundations document" link.
- CONFIG-REFERENCE.md `forbidden_actions` YAML example mismatch with the schema.
- Marker filename drift in CONCEPTS.md (`circuit-breaker-tripped.md` to `circuit-tripped.md`) — implementation has always used the shorter name.

## [1.0.0] — 2026-06-01

The feature-complete release. Everything originally scoped for v1 is in.

### Known v1.0.0 limitations

- Sub-agent orchestration is deferred to a future release. CLI agents handle their own isolation (each `claude` / `cursor-agent` invocation is a separate process with its own context); `dualpass` v1.0 documents the sub-agent affordance in stage skills but does not orchestrate sub-agents directly.
- The cost ledger is described in CONCEPTS.md but not emitted by the controller. Parsing per-CLI cost output was too vendor-specific and too fragile to ship; revisit in v1.1.
- PWD halt-and-remediate and cumulative-count cascade — two reliability lessons inherited from a production source pipeline — live at the doctrine level (in REVIEWER skills) but are not enforced as built-in gates. v1.1 candidate.

### Added (since v0.2.0a2)

- **`dualpass status`** — reads `.dualpass-state/<unit>-events.jsonl` + lockfiles, renders a rich-formatted table (or `--json`) showing per-unit state: `in-flight`, `completed`, `paused-at-breakpoint`, `blocked`, `circuit-tripped`, `stale-lock`, or `unknown`. Stale-lock detection uses `os.kill(pid, 0)` liveness.
- **Circuit breaker** — tracks SHA-256 of each round's artifact. When the author keeps producing identical content AND the reviewer keeps rejecting for `circuit_breaker.max_no_progress_relaunches` consecutive rounds, halts with `circuit_breaker_tripped` and drops a human-readable diagnostic at `.dualpass-state/<unit>-circuit-tripped.md`. Streak resets on the first new artifact.
- **Dual-pass parallel reviewer** — when `stage.dual_pass_reviewer: true`, the controller spawns two reviewer invocations in parallel via `ThreadPoolExecutor`. Both must return `approved` for the stage round to pass. Distinct `pass_label` values (`a`/`b`) disambiguate the per-reviewer artifact filenames so concurrent writes don't collide. Cross-vendor fallback applies independently to each pass.
- **`dualpass retro`** — single-unit mode (`--unit`) opens or seeds `docs/_project/RETROSPECTIVES/<unit>.md` (template pre-populated with the unit's run summary). Range mode (`--range '001..010' --output ...`) aggregates per-unit retros into a rollup with frontmatter, TOC, and concatenated bodies. Range parser handles zero-padded numeric ranges with or without prefixes.
- **`dualpass propose-dag`** — interactive walkthrough (4 questions) that writes `docs/_project/DAG-PROPOSAL.md` — a markdown sketch with shell-script implementation pattern. v1 explicitly stops at scoping; DAG execution is intentionally out of scope. `--non-interactive` mode for scripts.
- **Three background watchers (`research` / `prompt` / `handoff`)** — `dualpass watcher start <name>` daemonizes via double-fork + setsid, writes a PID file, and polls `.dualpass-state/` every N seconds (configurable). Each watcher auto-resumes units paused at THAT stage's breakpoint when a `<unit>-approved-<stage>.md` marker appears AND no pipeline lockfile is held. Triggered runs spawn `dualpass run --from-stage <stage> --ignore-breakpoints` via `subprocess.Popen(start_new_session=True)`. **State seeding** on first start writes `<unit>-handled-<stage>.md` for every pre-existing approval marker so the watcher doesn't stampede the historical backlog. **Idempotency**: handled-markers prevent double-trigger across poll cycles. **`--foreground`** flag keeps the watcher attached for debugging.
- 32 new tests since v0.2.0a2 (status + breaker + dual-pass + retro + propose-dag + watcher-loop). Total **149 passing** across Python 3.12/3.13 on Ubuntu and macOS.

### Changed (since v0.2.0a2)

- Every previously-stub CLI command is now functional. No remaining `not yet implemented` messages.
- `StageContext` gains an optional `pass_label: str | None` field used by the dual-pass parallel reviewer to disambiguate concurrent artifact writes.
- `Development Status` classifier bumped from `Pre-Alpha` to `Production/Stable` in `pyproject.toml`.

## [0.2.0a2] — 2026-06-01

### Added — the headline feature

- **`dualpass run --provider live` is now functional.** Real subprocess-based author + reviewer invocations against whatever CLIs `config/agents.yaml` points at (`claude`, `cursor-agent`, `codex`, anything that accepts `-p <prompt>`).
- **Cross-vendor reviewer fallback.** When the primary reviewer returns output matching any of its configured `exhaustion_patterns` (default `[resource_exhausted]`) for `activate_after_consecutive_exhausted` consecutive calls, the harness transparently swaps to the `reviewer_fallback` role — usually a different vendor — so review never silently drops. The exhaustion streak resets on any clean response, so a flapping primary doesn't permanently get demoted.
- **Transient retry.** Each role can declare `transient_retry_patterns` (e.g. `ETIMEDOUT`, `[unavailable]`) and a bounded `transient_retries` count. The harness retries the same role on those patterns before counting them toward exhaustion.
- **Verdict parsing.** Reviewer responses are scanned for a final `Verdict: approved | rejected | blocked` line. Unrecognizable responses default to `blocked` (the conservative choice — forces operator review).
- **Stage-skill injection.** The harness reads `skills/<stage>/SKILL.md` and `skills/<stage>/REVIEWER.md`, wraps each in a `<skill>` block, and embeds them in the author / reviewer prompts. A missing skill file logs a warning and proceeds with an empty skill (the LLM still has the stage name and unit ID).
- **Injection-safe command templates.** `command:` strings in `agents.yaml` are `shlex.split` and `{prompt}` placeholders replaced with the prompt as a single argv element. No shell interpretation, no string concatenation.
- **Diagnostic artifact headers.** Every artifact and review file written by `LiveProvider` carries three HTML comments at the top: `dualpass-served-by`, `dualpass-attempts`, `dualpass-returncode`. Makes failures auditable without reading event logs.
- 22 new tests + 1 controller-level end-to-end test against fake shell-script CLIs. Total 105 across the matrix.

### Changed

- `providers.get_provider` now takes `agents_config` keyword. Mock ignores it; live requires it.
- `controller.run_unit` threads `cfg.agents` into the provider factory.

## [0.2.0a1] — 2026-06-01

### Added

- **`dualpass watcher status` and `dualpass watcher stop` are now functional.** Status reports `running` / `stopped` / `stale-pid` per watcher with stale-PID detection via `os.kill(pid, 0)`. Stop signals SIGTERM and cleans up stale pidfiles.
- PID-file lifecycle helpers in `src/dualpass/watcher.py`: atomic create via `O_CREAT | O_EXCL`, stale-pid detection, signal delivery. The fs-watching loop itself remains deferred to v0.3.0.
- 11 new tests covering status reporting, stale-pid handling, stop semantics (including a real `multiprocessing.Process` fork→stop verification), and the CLI surface. Total 82 across the repo.

### Changed

- `dualpass watcher start` and `dualpass watcher restart` are the only remaining stubs in this command group. They cite the v0.3.0 milestone explicitly in their stub message.

## [0.2.0a0] — 2026-06-01

### Added

- **`dualpass run` is now functional** with the mock provider. End-to-end stage chain execution: research → outline → spec → prompt → code → audit → handoff. Honors project-level breakpoints (`--ignore-breakpoints` to run through). Resumable via `--from-stage <name>`.
- **`src/dualpass/providers/`** — provider abstraction. `Provider` ABC, `StageContext`, `AuthorResult`, `ReviewResult`, `ReviewVerdict`. Single shipping implementation: `MockProvider` (deterministic, offline, writes real artifacts). Live (subprocess-based) provider deferred to a later milestone.
- **`MockScript`** — per-stage scripting so tests can exercise reject/retry loops without randomness. Cursor sticks at the last verdict when the script is shorter than the round count.
- **`src/dualpass/controller.py`** — full `run_unit` implementation: acquires single-flight lockfile, walks stages in order, honors breakpoints + `--from-stage`, retries up to `max_rounds` on rejection, halts cleanly on max-rounds-exhausted, releases lock in `finally`.
- **`src/dualpass/observability.py`** — append-only JSONL event stream at `.dualpass-state/<unit>-events.jsonl`. Closed `EventType` vocabulary (`unit_started`, `stage_round_started`, `stage_completed`, `stage_revision_requested`, `stage_blocked`, `breakpoint_hit`, `lockfile_*`).
- **`src/dualpass/memory.py`** — atomic `acquire_lock` (via `os.O_CREAT | os.O_EXCL`), `release_lock`, `read_lock`, `units_dir` helpers. State layout under `.dualpass-state/<unit>/<stage>-{artifact,review}-v<round>.md`.
- 26 new tests (7 mock provider + 14 controller + library/CLI surface). Total 79 across the repo.

### Changed

- `run` and `reviewer.review` stub messages updated to clarify v0.2.0a0 scope (mock works; live lands later).

## [0.1.0a2] — 2026-06-01

### Added

- `dualpass init <path>` — now functional. Scaffolds a new project from the bundled `coding-agent` example. Rewrites `project_name` in `config/dualpass.json` to the target directory's basename (or an explicit `--project-name`). Creates missing parent directories. Refuses to overwrite a populated target (ignores `.git/`, `.DS_Store`, `.gitkeep`). Skips `__pycache__/` and `.DS_Store` noise during copy. Post-init config is guaranteed to validate.
- `src/dualpass/_init.py` — library entry point (`run_init`, `format_next_steps`, `InitError`, `InitResult`) usable independently of the CLI.
- Hatchling `force-include` wires `examples/coding-agent/` → `dualpass/_templates/coding-agent/` in the built wheel so init works on a fresh `pip install` where no `examples/` directory is present.
- 11 new tests covering happy paths, target-empty edge cases, populated-target refusal, project-name rewrite, post-init config validity, and end-to-end `init → doctor` chain.

### Changed

- Stub command list shrunk: `init` no longer emits "not yet implemented".

## [0.1.0a1] — 2026-06-01

### Added

- Config loader + validator with bundled JSON Schemas for all four config files (`dualpass.json`, `agents.yaml`, `stages.yaml`, `permissions.yaml`)
- Typed dataclasses for downstream consumption (`ProjectConfig`, `AgentsConfig`, `StagesConfig`, `PermissionsConfig`, `LoadedConfig`)
- Cross-file validation: predecessor references, breakpoint stage names, per-stage round overrides
- `dualpass doctor` — now functional. Probes Python version, agent CLI presence (`claude`, `cursor-agent`, `codex`), state directory writability, and config validity. Exits 0 on healthy, 1 on any failure
- `dualpass config validate` — now functional. Validates every config file and prints all errors in one pass with `file:path: message` format
- Tests for the config loader/validator and both functional CLI commands

### Changed

- `--version` is now `0.1.0a1`
- Stub command list shrunk: `doctor` and `config validate` no longer emit "not yet implemented"

## [0.1.0a0] — 2026-06-01

### Added

- Repository scaffolding: directory tree, LICENSE (Apache 2.0), README, pyproject.toml, .gitignore
- Documentation skeletons: CONCEPTS.md, RUNBOOK.md, CONFIG-REFERENCE.md
- Project-doc templates for `dualpass init`
- Package skeleton at `src/dualpass/` with module stubs
- CLI surface with command stubs (no implementation yet — emits NotImplementedError with guidance)
- Example project skeleton at `examples/coding-agent/`
- One smoke test confirming CLI is importable and `--help` exits cleanly
