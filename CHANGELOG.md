# Changelog

All notable changes to dualpass are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned for 0.1.0 (v1)

- Three background watchers with real fs-watching loop
- Circuit breaker (no-progress detection across consecutive failed rounds)
- `dualpass status`, `retro`, `propose-dag` commands

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
