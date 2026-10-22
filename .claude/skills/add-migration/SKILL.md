---
name: add-migration
description: Use when changing the database schema in this k8s-fastapi-starter service — adding, altering, or dropping tables or columns, or any change that needs an Alembic migration. Covers generating, reviewing, and applying the migration with backward-compatible expand-then-contract rules.
---

# Add a database migration

Read `SPEC.md` §7 (database migrations) first. Alembic uses the async template;
autogenerate targets `Base.metadata` in `src/app/models.py`.

## Steps

1. **Edit the models** — make the schema change in `src/app/models.py` (ORM
   models use `Mapped[...]` + `mapped_column(...)`; see the `Item` model).

2. **Generate the migration** —
   ```
   uv run poe migration "add widgets table"
   ```
   This runs `uv run alembic revision --autogenerate -m "..."` and writes a new
   file in `migrations/versions/` (existing baseline: `0001_create_items.py`).

3. **REVIEW the generated file** — autogenerate misses things (server defaults,
   enums, index/constraint renames, data backfills, non-nullable columns on
   populated tables). Open the file in `migrations/versions/` and fix the
   `upgrade()` / `downgrade()` ops by hand as needed.

4. **Keep it backward-compatible — expand then contract.** Old and new pods
   overlap during a rollout, so a single migration must work with the previously
   released app. Add columns as nullable or with defaults first; split a rename
   or a drop across multiple releases (expand, migrate data, then contract in a
   later migration). Write `downgrade()`, but note downgrades are **not** run
   automatically in production.

5. **Apply locally** — start services and upgrade:
   ```
   uv run poe up
   uv run poe db-upgrade
   ```
   (`db-upgrade` runs `uv run alembic upgrade head`.)

6. **Never edit a migration that has already been released.** Add a new one.

7. **Gates & commit** — run `uv run poe check`, update `docs/` if
   behavior is user-visible, and commit with a Conventional Commit, e.g.
   `feat(db): add widgets table`.

## In-cluster behavior

The migration runs in the cluster via `deploy/k8s/base/migration-job.yaml`
(an Alembic `upgrade head` Job using the same image and Secret) **before** the
Deployment rollout. A failed migration stops the rollout. This is why
expand-then-contract compatibility matters.

See `AGENTS.md` and `SPEC.md` §7 for the full rules.
