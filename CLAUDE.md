# CLAUDE.md

**See [`AGENTS.md`](AGENTS.md) for contributor and agent instructions**, which
distill [`SPEC.md`](SPEC.md) (the source of truth — read it first).

## Claude Code notes

- Drive everything through the poe tasks (`SPEC.md` §12) so local == CI.
  Before committing: `uv run poe check`.
- Use `uv` for all Python (`uv add`); never `pip`, never hand-edit `uv.lock`.
- Conventional Commits are required (they feed git-cliff).

## Available project skills (`.claude/skills/`)

- **add-endpoint** — use when adding a new FastAPI route/resource.
- **add-migration** — use when changing the database schema.
- **release** — use when cutting a release.
