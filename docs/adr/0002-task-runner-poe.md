# ADR 0002: Task runner — poethepoet instead of a Makefile

- **Status:** Accepted
- **Scope:** The documented task interface (SPEC §12)
- **Supersedes:** The Makefile that originally provided these targets

## Context

The repository needs one documented set of entry points (install, lint, test,
audit, migrate, release, …) that behave identically for developers and in CI.
The original implementation was a `Makefile`. The rest of the project's tooling
(uv, ruff, mypy, pytest) is configured in `pyproject.toml`, and the project is
uv-managed.

## Decision

Use **poethepoet (`poe`)** as the task runner. Tasks live in `pyproject.toml`
under `[tool.poe.tasks]` and run with `uv run poe <task>`. The `Makefile` was
removed and all call sites (CI workflows, pre-commit, docs, `AGENTS.md`,
`CLAUDE.md`, and the bundled skills) now use `uv run poe`.

Alternatives considered: **taskipy** (lighter, but no task graph / sequences),
**invoke** (tasks in a separate `tasks.py`, not `pyproject.toml`), and
**just** (not Python, a separate binary). poe won because its tasks live in
`pyproject.toml` beside the other tool config, it supports task sequences
(`check`, `check-all`), and it needs no non-Python dependency.

## Consequences

- One `uv run poe <task>` interface, cross-platform (no `make` dependency, so it
  also works on Windows).
- `poe` is a dev-group dependency (`poethepoet`); it is not shipped in the
  runtime image.
- Two behavioral notes carried over from the Makefile:
  - `migration` is a **shell** task so the message survives as one quoted
    argument: `uv run poe migration "add widgets table"` (positional, replacing
    the Makefile's `m="..."`). A `cmd`-style task would split the message on
    spaces.
  - `test` runs the full `pytest` suite (unit + integration), matching CI, so it
    needs `uv run poe up` first locally; `uv run poe check` is the quick
    lint + typecheck + test loop.
