# Contributing to dualpass

Thanks for your interest. dualpass is a small, opinionated project; the contribution surface is intentionally narrow.

### Python 3.14 note

Editable installs (`pip install -e .`) appear to succeed on Python 3.14 + macOS but leave the package silently unimportable: Python 3.14's site.py skips `.pth` files carrying the auto-applied `com.apple.provenance` extended attribute, and macOS sets that xattr on every newly-written file in the venv. The xattr can't be reliably stripped (`xattr -d` is a no-op for `com.apple.provenance`).

**Workarounds:**

- **Run tests directly from the repo root** — `conftest.py` adds `src/` to `sys.path` automatically, so `.venv/bin/pytest tests/` works regardless of install state.
- **Wheel install** — `pip install dualpass` (once published) works on 3.14; no editable-mode pth file involved.
- **Set `PYTHONPATH=src`** explicitly when invoking the CLI from the repo: `PYTHONPATH=src .venv/bin/dualpass ...`.
- **Use Python 3.12 or 3.13** for editable development — both predate the `.pth` xattr check.

The package metadata declares `requires-python = ">=3.12"` (no upper bound), so the wheel installs cleanly on 3.14. The pin is intentionally loose; the issue is local development, not distribution.

## Before opening a PR

1. **Open an issue first** for anything larger than a typo or a bug fix. Design changes (new commands, new config keys, new stage primitives) need to be discussed before code lands — the project's value is its opinionated defaults, not its surface area.
2. **Run the test suite locally.** With `uv`: `uv run pytest`. With stdlib venv:

   ```bash
   python -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/pytest tests/ -v
   .venv/bin/ruff check src/ tests/
   ```

3. **Run the linter:** `uv run ruff check . && uv run ruff format --check .` (or the `.venv/bin/ruff` invocation above).
4. **Update CHANGELOG.md** under `[Unreleased]`.

## Scope of contributions

**In scope** (open a PR, ideally after a short issue thread):

- New built-in gates (drop-in modules under `src/dualpass/gates/` with tests).
- New `EventType` entries (with discussion — these are part of the observability contract).
- New example projects under `examples/` (self-contained, runnable with the mock provider).
- Doc improvements (CONCEPTS, RUNBOOK, CONFIG-REFERENCE, examples).
- Test coverage for under-tested paths.
- CLI templates for additional agent providers (contract documented in CONFIG-REFERENCE.md).

**Likely out of scope without prior discussion:**

- Vendor-SDK integration. The CLI-template contract is a load-bearing design choice (see README).
- DAG execution engines or alternative orchestration topologies. dualpass is intentionally a linear stage pipeline.
- Hosted / SaaS layers, multi-tenant servers.
- Web UIs or visual builders.
- New core abstractions in `src/dualpass/{controller,stages,reviewer,context,memory}.py`. These are the load-bearing surfaces and changes compound.

**Always welcome:**

- Bug reports with reproduction steps.
- Doc fixes (typos, clarifications, broken links).
- New examples that exercise real-world stage shapes.
- Retros from your own pipelines — what worked, what didn't, what surprised you.

**Not accepted:**

- AI-generated docs or PRs without human review. Author your own writing.

## Code style

- Python 3.12+ syntax (use `|` unions, `match` statements where they help).
- Type hints on every public function. Internal helpers may omit them.
- `from __future__ import annotations` at the top of every module.
- No `print()` — use `rich` or the project's `observability` module.
- Tests live in `tests/` mirroring `src/dualpass/` structure.

## Reviewer

All PRs are reviewed by a human before merge. If the PR author is an LLM agent, the PR description should disclose that and identify the human responsible for the contribution.

## License

By contributing, you agree your contribution is licensed under Apache 2.0 (the project license). The Apache 2.0 patent grant flows from your contribution to project users.
