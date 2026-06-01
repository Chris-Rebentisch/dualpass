# Changelog

All notable changes to dualpass are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned for 0.1.0 (v1)

- Controller with single-flight lockfile, circuit breaker, auto-relaunch
- Fixed-cycle stage abstraction with named stages, configurable per project
- Dual-pass reviewer with cross-vendor fallback (D479-style)
- Three background watchers (research-complete → outline, prompt-drafts, handoff-finals) with §6.10-style PID-parsing fix and split-parent/lockfile guards
- Mock provider for offline smoke tests
- `dualpass init`, `doctor`, `run`, `status`, `retro`, `propose-dag` commands
- One full example project: `examples/coding-agent/` (anonymized GrACE 7-stage pattern)
- Anthropic skill format for stage skills
- Apache 2.0 license

## [0.1.0a0] — 2026-06-01

### Added

- Repository scaffolding: directory tree, LICENSE (Apache 2.0), README, pyproject.toml, .gitignore
- Documentation skeletons: CONCEPTS.md, RUNBOOK.md, CONFIG-REFERENCE.md
- Project-doc templates for `dualpass init`
- Package skeleton at `src/dualpass/` with module stubs
- CLI surface with command stubs (no implementation yet — emits NotImplementedError with guidance)
- Example project skeleton at `examples/coding-agent/`
- One smoke test confirming CLI is importable and `--help` exits cleanly
