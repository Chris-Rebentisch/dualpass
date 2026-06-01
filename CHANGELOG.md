# Changelog

All notable changes to dualpass are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Possible future work (open to feedback):

- Hosted variant (out of scope for v1 by design — currently local-filesystem only)
- Per-reviewer focus prompts for dual-pass (currently both reviewers see the same prompt; the contrast comes from LLM nondeterminism and cross-vendor fallback)
- Sub-agent orchestration (currently documented affordance only — CLI agents handle their own isolation)
- Built-in cost ledger (parsing per-CLI cost output was too vendor-specific to ship in v1; ROADMAP candidate)
- PWD halt-and-remediate and cumulative-count cascade as enforced built-in gates (currently inherited at the doctrine level only)
- Disk-unblock polling for hung cursor-agent reviewer subprocesses (mirrors `run_subprocess_with_reviewer_disk_unblock` in the source pipeline). v1.0.2 reads the verdict from the on-disk review file, so a hung subprocess doesn't lose the verdict — but it does still tie up the slot until timeout. A polling-and-terminate pattern would shorten that window.

## [1.0.5] — 2026-06-01

A live-provider dogfood at chunk-77 surfaced a more fundamental flaw than v1.0.4's preamble defect: the audit→handoff gate model itself was inverted. v1.0.4 (inherited from earlier versions) treated every PASS_WITH_DEVIATIONS audit as an architect decision — handoff refused to draft until a `*-deviations-accepted.md` file landed. In practice most audit deviations are mechanical or structural and should round-trip through the code stage without bothering the architect; only true architectural divergence (the code chose a different design point than the spec ratified) actually needs architect attention. v1.0.5 reshapes the audit verdict, the controller routing, the CLI surface, and both audit + handoff skills to match.

### Changed

- **Audit verdict shape — three buckets, not two.** The bundled `examples/coding-agent/skills/audit/SKILL.md` now emits a machine-stable verdict line of `**Verdict:** PASS | NEEDS_REMEDIATION | ARCHITECTURAL_DIVERGENCE` (was: `PASS | PASS_WITH_DEVIATIONS | FAIL`). Every finding carries one of four severity tags via HTML comment: `<!-- severity: mechanical|structural|organizational|architectural -->`. The verdict is derived from the strongest applicable severity: any `architectural` → `ARCHITECTURAL_DIVERGENCE`; otherwise any other → `NEEDS_REMEDIATION`; otherwise `PASS`. Audit reviewer (`audit/REVIEWER.md`) rewritten to verify honest triage — under-tagging architectural divergence and over-tagging mechanical issues are both blockers.
- **Controller routes on the audit verdict.** `src/dualpass/controller.py` parses the audit FINAL body after the audit reviewer signs off. PASS advances to handoff. NEEDS_REMEDIATION rewinds to the `code` stage with the audit FINAL on disk as feedback, bounded by `max_audit_iterations` (default 4). ARCHITECTURAL_DIVERGENCE writes a stuck marker and halts immediately. An audit FINAL with no recognizable verdict line halts conservatively for operator inspection.
- **Handoff skill always drafts.** The bundled `examples/coding-agent/skills/handoff/SKILL.md` no longer refuses on PASS_WITH_DEVIATIONS — there's nothing to refuse on. Two output shapes: WITHOUT-DEVIATIONS (no sidecar present) and WITH-DEVIATIONS (architect ran `accept-divergence`, sidecar present, §10a Accepted Divergences carries the architect's rationale + auditor's findings verbatim). Handoff reviewer rewritten to verify §10a fidelity against the sidecar.

### Added

- **`max_audit_iterations: int = 4`** field on `ProjectConfig` (`src/dualpass/config.py`) and in `src/dualpass/schemas/dualpass.json`. Bounds the audit-remediation loop independently of per-stage `max_rounds`. Example `config/dualpass.json` ships with the explicit `4`.
- **`AuditVerdict` literal type + `_parse_audit_verdict()`** in `src/dualpass/controller.py`. Reads the audit FINAL body; matches `**Verdict:** PASS|NEEDS_REMEDIATION|ARCHITECTURAL_DIVERGENCE` case-insensitively; returns `unknown` on missing or unrecognized verdict.
- **`_write_audit_routing_marker()`** in `src/dualpass/controller.py`. Writes one of two architect-intervention stuck markers: `<unit>-stuck-architectural-divergence.md` or `<unit>-stuck-audit-loop-exhausted.md`. Both include copy-pasteable `dualpass remediate` and `dualpass accept-divergence` commands.
- **`dualpass remediate --unit <id>`** CLI subcommand. Architect disposition for "this IS fixable in code." Clears the stuck marker; relaunches from the `code` stage (overridable via `--from-stage`).
- **`dualpass accept-divergence --unit <id> --rationale "..."`** CLI subcommand. Architect disposition for "ship this as documented divergence." Writes `.dualpass-state/<unit>/divergence-accepted.json` with architect + rationale + `accepted_at` + the auditor's `architectural`-severity findings reproduced verbatim, clears the stuck marker, relaunches from `handoff` (or `--no-run` to land the sidecar only).
- **`MockScript.audit_verdicts`** field on `src/dualpass/providers/mock.py`. Test-only — lets the mock provider drive the audit verdict line across stage re-entries. Defaults to `["PASS"]`.
- **12 new tests** under `tests/test_audit_routing.py` covering all three verdict paths, `max_audit_iterations` exhaustion, unknown-verdict halt, both architect CLI subcommands (including `--no-run`), the verdict parser, and the default-config field.

### Removed

- **Legacy `PASS_WITH_DEVIATIONS` and `FAIL` verdict strings** from the audit skill. The pre-v1.0.5 strings are no longer recognized by the controller's routing path. Audits that emit them halt as `unknown`.
- **Handoff refusal-stub pattern.** The bundled `handoff/SKILL.md` no longer ships a "gate closed → write stub" path.

### Migration

Breaking for projects that pinned the legacy audit verdict strings or built their own tooling around the `*-deviations-accepted.md` file. Otherwise additive: existing projects pick up the new behavior on their next audit run.

Projects that customized the audit or handoff skill text need to replace the bundled files (or merge the v1.0.5 shape manually). Projects that just `dualpass init`'d the example and never edited the skills get the new behavior for free.

`config/dualpass.json` does NOT need to be updated — `max_audit_iterations` defaults to 4 when omitted.

### Tests

227 → 239 passing.

## [1.0.4] — 2026-06-01

A second live-provider dogfood run against the chunk-77 scope brief surfaced the next layer of friction: the v1.0.2 diagnostic header (three HTML comment lines prepended to every artifact) was visible to claude during revision rounds. Claude read its previous artifact (with the header), treated the header as part of the artifact structure, and reproduced extra copies in its own Write-tool output. The v1.0.2 strip-one-prepend-one logic couldn't keep up: after each revision round the file accumulated more diagnostic-header triplets, the reviewer correctly rejected on the duplicate-preamble defect, and spec stage ran 7+ rounds before max_rounds halted without converging. The author IS iterating responsibly — cursor's substantive design-severity findings WERE getting addressed — but the harness was reintroducing the same mechanical fault each round.

### Changed

- **`src/dualpass/providers/live.py`** — `_resolve_artifact_path` now writes the artifact body as pure markdown and routes diagnostic info (`served_by`, `attempts`, `returncode`) to a `<stage>-artifact-v<N>.meta.json` sidecar via the new `_write_meta_sidecar` helper. Keeping harness metadata out of the file the agent sees prevents the recursive reproduction loop.
- **`_sanitize_artifact_body`** is the new shared sanitization helper. Three passes in order: (1) strip one OR MORE consecutive diagnostic-header triplets at the top (the v1.0.3 stacking bug — `_DIAGNOSTIC_HEADER_RE` now uses `(triplet)+` instead of single-triplet); (2) JSON envelope unwrap (mirrors `parse_json_stdout` + `extract_cli_payload`); (3) preamble strip — when YAML frontmatter is present, discard prose between start-of-file and the first `---` line (the v1.0.3 narrate-before-structure pattern claude couldn't override even when the gate feedback named it). H1+bold-line artifacts have no `---` anchor, so their leading prose is the artifact and stays untouched.

### Added

- `_meta_sidecar_path()`, `_write_meta_sidecar()`, `_sanitize_artifact_body()` helpers in `src/dualpass/providers/live.py`.
- 4 new tests covering: stacked-header strip, preamble strip, H1+bold-line passthrough, sidecar materialization. Existing inline-header assertions migrated to sidecar.

### Migration

Backward compatible. Artifacts from v1.0.0–v1.0.3 carry the diagnostic header inline; v1.0.4 reads them via the strip-on-load path and rewrites in the new clean shape on the next revision round. No data loss. The `.meta.json` sidecar files are additive — old projects don't have them yet, new revisions will create them.

### Tests

224 → 227 passing.

## [1.0.3] — 2026-06-01

A second live-provider dogfood test surfaced an architectural mismatch in the bundled gate stack: v1.0.2's `check-frontmatter` gate fired on every stage, but real LLM authors (claude in particular) reliably produce YAML frontmatter only on a subset of artifact shapes. On outline / spec / prompt / audit / handoff, claude's narrative instinct emits a preamble paragraph before any structured block, which makes a `\A---` frontmatter check unsatisfiable even when the artifact body is otherwise correct. The frontmatter gate is fine; the bundled default just enforced it on stages where the proven production pattern doesn't.

### Changed

- **`examples/coding-agent/config/stages.yaml`** — frontmatter policy aligned with proven multi-cycle pattern from a production source pipeline:
  - `research` stage keeps `check-frontmatter` (research files are machine-parsed downstream — frontmatter IS the contract).
  - `code` stage keeps `check-marker-frontmatter` (the build-complete marker is machine-parsed by the controller's halt logic — strict YAML required).
  - `outline`, `spec`, `prompt` drop `check-frontmatter`. These use markdown H1 + bold-prefix metadata lines instead — the natural shape claude tends to produce.
  - `audit`, `handoff` drop `check-frontmatter` and ship with empty `preflight_gates: []`. Sectional markdown reports verified by humans + targeted parse scripts at handoff time.
  - Stage comments now document the per-stage header-shape contract so operators understand the policy.

### Why this matters

Forcing YAML frontmatter on stages where claude narrates is a recipe for stalled rounds: the gate catches a real mechanical violation, but the agent can't override its narration instinct round-over-round and the run halts. Matching the gate stack to the agent's natural output shape preserves mechanical discipline where it matters (research, code marker) and removes pointless friction where it doesn't (outline / spec / prompt / audit / handoff). Lesson distilled from a production pipeline that ran this H1+bold-line shape across 80+ build cycles.

### Tests

224 passing (unchanged — existing tests didn't pin the bundled gate stack).

## [1.0.2] — 2026-06-01

The first live-provider dogfood test surfaced a fundamental design mismatch: the v1.0.0 LiveProvider assumed agents stream markdown to stdout, but real agent CLIs (claude, cursor-agent) write artifacts to disk via their own tools and emit status summaries to stdout. The provider was destructively overwriting agent-written files with the status-summary stdout. This release ports the proven file-on-disk-first patterns from a production source pipeline.

### Added

- **`_resolve_artifact_path`** in `src/dualpass/providers/live.py` — file-on-disk-first artifact resolution. If the agent's own tools (Write, Bash) created the expected artifact file, that file IS the artifact; the diagnostic header is prepended inline. If no file appeared, the provider falls back to writing stdout. Direct port of `infer_latest_stage_artifact` semantics from the source pipeline.
- **`_unwrap_json_envelope`** in `src/dualpass/providers/live.py` — when stdout begins with `{` and parses as a JSON object with a string `result` field (e.g. `claude --output-format json` or `cursor-agent --output-format json`), the wrapped `.result` content is unwrapped before writing. Direct port of `parse_json_stdout` + `extract_cli_payload`.
- **`_verdict_from_text`** in `src/dualpass/providers/live.py` — reviewer verdict resolution now reads the on-disk review file body first before falling back to stdout. cursor-agent reliably writes the review to disk but its stdout is unreliable; the file body is the authoritative source. Direct port of `review_body_signals_approved`.
- 15 new tests under `tests/test_providers_live.py` covering the three helpers, the integration path, and backward compatibility with stdout-streaming skills.

### Changed

- **`examples/coding-agent/config/agents.yaml`** — dropped `--output-format json` from all four roles. The provider unwraps the envelope defensively if a user re-adds it, but the default no longer enshrines the v1.0.1 footgun.
- **`examples/coding-agent/config/stages.yaml`** — `research.reviewer_skill: null` by default. Research is exploration, not judgment-against-spec; LLM review on research burns budget without catching the load-bearing failures (those surface in outline/spec). `skills/research/REVIEWER.md` ships as an opt-in template for projects that want to invest in research review. Lesson inherited from a production source pipeline that ran research-without-reviewer across 80+ build cycles.
- **`src/dualpass/providers/live.py` module docstring** — no longer claims "no JSON-envelope assumptions" or "stdout-only." Now documents the two-source artifact resolution policy and cites the source pipeline functions each helper ports.

### Fixed

- v1.0.1 LiveProvider destructively overwrote files the agent wrote via its own Write tool with the agent's stdout status summary — the actual artifact content was lost. v1.0.2 preserves on-disk content.
- v1.0.1 example `agents.yaml` shipped with `--output-format json` on commands that the LiveProvider's docstring explicitly disclaimed handling for. The example now matches the provider's actual contract.
- v1.0.1 example `stages.yaml` shipped with a reviewer on the research stage, which a real run of a production scope brief revealed to be wasted budget (no judgment standard for exploration).

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
