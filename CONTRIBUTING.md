# Contributing to dualpass

Thanks for your interest. dualpass is a small, opinionated project; the contribution surface is intentionally narrow.

## Before opening a PR

1. **Open an issue first** for anything larger than a typo or a bug fix. Design changes (new commands, new config keys, new stage primitives) need to be discussed before code lands — the project's value is its opinionated defaults, not its surface area.
2. **Run the test suite locally:** `uv run pytest`.
3. **Run the linter:** `uv run ruff check . && uv run ruff format --check .`.
4. **Update CHANGELOG.md** under `[Unreleased]`.

## What we accept

- Bug fixes with regression tests.
- Documentation improvements (CONCEPTS, RUNBOOK, CONFIG-REFERENCE, examples).
- New gate plugins (drop-in scripts under `src/dualpass/gates/` with tests).
- Additional example projects under `examples/` (must be self-contained and runnable with the mock provider).
- CLI templates for additional agent providers (the project's CLI-template contract is documented in CONFIG-REFERENCE.md).

## What we don't accept

- New core abstractions in `src/dualpass/{controller,stages,reviewer,context,memory}.py` without prior discussion. These are the load-bearing surfaces and changes compound.
- Switching from CLI-template invocation to vendor SDKs. The CLI contract is a load-bearing design choice (see README).
- Visual builders, web UIs, hosted-service shims. These are explicit non-goals.
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
