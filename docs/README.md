# k8s-fastapi-starter

A template for a Kubernetes-deployed FastAPI service backed by PostgreSQL and Redis. A new service created from
it gets typed Python, versioned migrations, security gates, conventional-commit changelogs, and a Backstage
entry with TechDocs.

- **Specification (source of truth):** `SPEC.md`
- **Documentation:** This page is the MkDocs and Backstage TechDocs homepage. Preview with `uv run poe docs`.

## What you get

- **FastAPI** app (`app.main:create_app`) with `/healthz` (liveness) and `/readyz` (Postgres and Redis readiness).
- A sample `items` CRUD resource that demonstrates the database session, the Redis cache-aside pattern, and migrations.
- **uv** for dependencies, **Ruff** for lint and format, **mypy --strict** for typing, **pytest** for tests.
- **Alembic** migrations, applied by a Kubernetes Job before each rollout.
- **Kustomize** overlays for `dev` and `prod`, with **SOPS**-encrypted secrets.
- **GitHub Actions** for CI, release, scheduled security scans, and secrets synchronization; Argo CD handles GitOps deployments.

## Tech stack

| Area | Technologies |
|---|---|
| API and configuration | [Python 3.13](https://docs.python.org/3.13/), [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://uvicorn.dev/), [Pydantic](https://docs.pydantic.dev/latest/) and [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) |
| Database and migrations | [PostgreSQL 17](https://www.postgresql.org/docs/17/), [SQLAlchemy 2.x (async)](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html), [asyncpg](https://magicstack.github.io/asyncpg/current/), [Alembic](https://alembic.sqlalchemy.org/en/latest/) |
| Cache | [Redis 7](https://redis.io/docs/latest/) with [redis-py (`redis.asyncio`)](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html) |
| Development tooling | [uv](https://docs.astral.sh/uv/), [poethepoet (`poe`)](https://poethepoet.natn.io/), [Docker Compose](https://docs.docker.com/compose/), [pre-commit](https://pre-commit.com/) |
| Code quality and testing | [Ruff](https://docs.astral.sh/ruff/), [mypy in strict mode](https://mypy.readthedocs.io/en/stable/), [pytest](https://docs.pytest.org/en/stable/), [pytest-asyncio](https://pytest-asyncio.readthedocs.io/en/stable/), [httpx](https://www.python-httpx.org/) |
| Containers and delivery | [Docker](https://docs.docker.com/), [Kubernetes](https://kubernetes.io/docs/), [Kustomize](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/), [Argo CD](https://argo-cd.readthedocs.io/en/stable/), [GitHub Actions](https://docs.github.com/en/actions), [GitHub Container Registry (GHCR)](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) |
| Secrets | [SOPS](https://getsops.io/docs/) with [AWS KMS](https://docs.aws.amazon.com/kms/latest/developerguide/overview.html), synced through [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html) to [External Secrets Operator](https://external-secrets.io/latest/) |
| Security checks | [pip-audit](https://github.com/pypa/pip-audit#readme), [gitleaks](https://github.com/gitleaks/gitleaks#readme), [Trivy](https://trivy.dev/docs/latest/guide/), [hadolint](https://github.com/hadolint/hadolint#readme), [kubeconform](https://github.com/yannh/kubeconform#readme), [kube-linter](https://docs.kubelinter.io/) |
| Monitoring | [Datadog APM (`ddtrace`)](https://docs.datadoghq.com/tracing/trace_collection/dd_libraries/python/), runtime metrics and optional log–trace correlation |
| Documentation and service catalog | [MkDocs](https://www.mkdocs.org/) with [Material](https://squidfunk.github.io/mkdocs-material/), [Backstage TechDocs](https://backstage.io/docs/features/techdocs/) |
| Releases and dependency updates | [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), [git-cliff](https://git-cliff.org/docs/), [Dependabot](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-version-updates) |

See `SPEC.md` §2 for the rationale behind each choice.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Docker with Compose. Python is pinned in `.python-version`, and uv installs it.

```bash
uv run poe install     # install dependencies
cp .env.example .env   # local configuration; never commit .env
uv run poe up          # start Postgres 17 and Redis 7
uv run poe db-upgrade  # apply migrations
uv run poe run         # API on http://localhost:8000 (see /healthz and /readyz)
```

Run `uv run poe` to list every task. Before you open a PR, run `uv run poe hooks` once, then `uv run poe check-all`.

## Where to go next

| If you want to... | Read |
|---|---|
| Run it on your machine | [Getting started](getting-started.md) |
| Understand the moving parts | [Architecture](architecture.md) |
| Set environment variables | [Configuration](configuration.md) |
| Contribute a change | [Contributing](contributing.md) |
| Understand the CI workflows | [Continuous integration](ci.md) |
| Change the database schema | [Database migrations](database-migrations.md) |
| Ship it to a cluster | [Deployment](deployment.md) |
| Handle credentials | [Secrets](secrets.md) |
| Understand the gates | [Security](security.md) |
| Monitor it (Datadog) | [Monitoring](monitoring.md) |
| Cut a release | [Releasing](releasing.md) |

## Creating a real repository from this template

This repo is a starter template: identity values (project name, author, GitHub org, Backstage owner, AWS/KMS
settings, …) are **placeholders**. Every value that must be supplied is a `PLACEHOLDER_<NAME>` token — list
them all with `rg PLACEHOLDER_`. The machine-readable registry of every placeholder (fill / rename / keep /
runtime / external) is `.template/placeholders.yml`.

The fastest path is to ask an AI agent to "initialize this template" — `AGENTS.md` tells it exactly
how (read the registry, prompt you for each value, replace, verify). To do it by hand:

1. Create a repository from this template.
2. Open `.template/placeholders.yml` and gather a value for each `fill` and `rename` entry. `PROJECT_SLUG` (default
   `k8s-fastapi-starter`) must be a valid DNS-1123 label and Python distribution name.
3. Replace every `PLACEHOLDER_*` token and rename the slug where the registry says (not in `SPEC.md`/`docs/README.md`/
   `AGENTS.md`). Set real secrets via `sops`, never in plaintext — see [Secrets](secrets.md). Set the AWS
   deploy role/region as GitHub Environment variables — see [Deployment](deployment.md).
4. Delete `.template/` and this "Creating a real repository…" section.
5. Run `uv run poe check`, confirm `rg PLACEHOLDER_` is empty, and decide the open items in SPEC.md section 13
   before the first deploy.

## Maintaining this overview

See [Working on the docs](contributing.md#working-on-the-docs) for preview commands,
page organization, and diagrams. This file is the single overview for the repository and TechDocs.

## Layout

See SPEC.md section 3 for the full tree. The main directories are `src/` (app), `migrations/` (Alembic),
`deploy/k8s/` (Kustomize), `docs/` (TechDocs), and `.github/workflows/` (CI, release, security, secrets sync).
