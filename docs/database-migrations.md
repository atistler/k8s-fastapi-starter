# Database migrations

Migrations use **Alembic** with the async template. `migrations/env.py` reads `DATABASE_URL` from
`app.config.Settings` and runs migrations through an async engine.

## Day-to-day commands

```bash
uv run poe up                              # Postgres must be running
uv run poe db-upgrade                      # uv run alembic upgrade head
uv run poe migration "add widgets table" # autogenerate a new revision
```

`uv run poe migration` runs `alembic revision --autogenerate -m "..."`. **Always review the generated file.**
Autogenerate misses some changes, such as data migrations, renamed columns, and some server defaults.

## In Kubernetes

`deploy/k8s/base/migration-job.yaml` is a Job that runs `alembic upgrade head` with the same image and the
same Secret as the app. The deploy sequence is:

1. Apply the Secret.
2. Apply the migration Job and wait for it to complete.
3. If the Job fails, stop. The Deployment is not updated.
4. Apply the overlay, which rolls out the new Deployment.

The Job name includes the image tag, and overlays patch it. Re-runs for a new tag therefore do not collide with old Jobs.

## Rules

- **Backward compatible.** Old and new pods overlap during a rollout, so each migration must work with the
  previous app version. Use expand, then contract: add the new column or table first, move the code, and
  drop the old column in a later release.
- **Never edit a released migration.** Add a new revision instead.
- **Downgrades are written but not run in production.** Keep them correct for local rollback.
- **Test migrations locally** against an empty database and against one with data before merging.
