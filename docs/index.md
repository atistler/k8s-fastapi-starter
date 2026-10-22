# K8s FastAPI starter

This repository is a **template** for a Kubernetes-deployed FastAPI service backed by PostgreSQL and Redis.
A new service created from it gets typed Python, versioned migrations, security gates, conventional-commit
changelogs, and a Backstage entry with TechDocs, without further tooling decisions.

## What you get

- **FastAPI** app (`app.main:create_app`) with `/healthz` (liveness) and `/readyz` (Postgres and Redis readiness).
- A sample `items` CRUD resource that demonstrates the database session, the Redis cache-aside pattern, and migrations.
- **uv** for dependencies, **Ruff** for lint and format, **mypy --strict** for typing, **pytest** for tests.
- **Alembic** migrations, applied by a Kubernetes Job before each rollout.
- **Kustomize** overlays for `dev` and `prod`, with **SOPS**-encrypted secrets.
- **GitHub Actions** for CI, release, scheduled security scans, and manual deploys.

## Where to go next

| If you want to... | Read |
|---|---|
| Run it on your machine | [Getting started](getting-started.md) |
| Understand the moving parts | [Architecture](architecture.md) |
| Set environment variables | [Configuration](configuration.md) |
| Change the database schema | [Database migrations](database-migrations.md) |
| Ship it to a cluster | [Deployment](deployment.md) |
| Handle credentials | [Secrets](secrets.md) |
| Understand the gates | [Security](security.md) |
| Monitor it (Datadog) | [Monitoring](monitoring.md) |
| Cut a release | [Releasing](releasing.md) |

The full specification, including every technology choice and the open questions, is in
`SPEC.md` at the repository root. Read it before changing a choice in the stack.
