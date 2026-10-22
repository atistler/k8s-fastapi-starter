# k8s-fastapi-starter — Specification

This repository is a **template** for a Kubernetes-deployed FastAPI service backed by PostgreSQL and Redis. This document records every technology choice, the reason for it, the repository layout, and the operating rules. It is the source of truth for the template. Agents working in this repo should read this file first.

---

## 1. Goals and non-goals

**Goals**

- A new service can be created from this template and deployed to Kubernetes with no further tooling decisions.
- Typed Python throughout, enforced in CI.
- Database schema changes are versioned and applied by a repeatable migration step.
- Security gates for Python dependencies, Python source, secrets, container images, and Kubernetes manifests.
- Conventional commits drive the changelog and release notes.
- The service is visible in Backstage with TechDocs.

**Non-goals (for now)**

- A specific cloud provider's managed database or cache. Postgres and Redis are reached by URL from config; local development uses Docker Compose.
- Business logic. The sample `items` resource exists only to prove the wiring.

GitOps via **Argo CD** is adopted (it was previously a non-goal); deployment is GitOps, not a CI apply job. See ADR 0001 and §10.

---

## 2. Technology choices

| Concern                        | Choice                                                                                                     | Why this and not the alternatives                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python version                 | **3.13**, pinned in `.python-version`                                                                      | Current stable with mature ecosystem support. `.python-version` is read by uv, pyenv, and the Dockerfile base-image tag, so one file controls all three.                                                                                                                                                                                                                                                                                                                                                                                                  |
| Dependency management          | **uv** (`pyproject.toml` + committed `uv.lock`)                                                            | Fast, single binary, lockfile by default, replaces pip/pip-tools/poetry for this use case.                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Web framework                  | **FastAPI**                                                                                                | Typed request/response models, OpenAPI generation, async-first.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ASGI server                    | **Uvicorn** (`uvicorn[standard]`)                                                                          | Reference ASGI server for FastAPI.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Settings                       | **pydantic-settings**                                                                                      | Typed, env-driven configuration; matches FastAPI's Pydantic models.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| PostgreSQL driver / ORM        | **SQLAlchemy 2.x (async) + asyncpg**                                                                       | Typed ORM with `Mapped[...]` annotations; asyncpg is the fastest async Postgres driver.                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Migrations                     | **Alembic** (async template)                                                                               | De facto standard for SQLAlchemy. Migrations run as a Kubernetes Job before each rollout (§7).                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Redis client                   | **redis-py** (`redis.asyncio`)                                                                             | Official client, native async, typed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Lint + format                  | **Ruff** (lint and format)                                                                                 | One fast tool replaces flake8, isort, black, pyupgrade, and most of bandit's rules.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Static typing                  | **mypy --strict** with the Pydantic plugin                                                                 | Strict mode is the bar for "typed Python". mypy is the most widely supported checker in CI tooling.                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Tests                          | **pytest**, **pytest-asyncio**, **httpx** (`ASGITransport`)                                                | Standard. httpx drives the ASGI app in-process.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Python dependency audit        | **pip-audit** against the locked export                                                                    | Official PyPA tool; audits the exact `uv.lock` versions with no environment drift (§6.1).                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Python source security         | **Ruff `S` rules** (bandit-equivalent) + **gitleaks** for secrets                                          | Ruff's `S` ruleset covers bandit checks without a second runtime; gitleaks catches committed credentials.                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Container image scan           | **Trivy** (image scan in CI)                                                                               | Scans OS packages and Python site-packages in the built image.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Dockerfile lint                | **hadolint**                                                                                               | Standard Dockerfile linter.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Kubernetes manifest validation | **kubeconform** (schema)                                                                                   | Validates rendered manifests against Kubernetes OpenAPI schemas, including CRDs listed in config.                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Kubernetes manifest security   | **kube-linter** (StackRox)                                                                                 | Flags privileged containers, missing probes, missing resource limits, running as root, and similar.                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Kustomize                      | Built into `kubectl` (`kubectl kustomize` / `kubectl apply -k`)                                            | No extra binary; overlays give dev/prod differences without templating.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Task runner                    | **poethepoet (`poe`)**, tasks in `pyproject.toml`, run via `uv run poe <task>`                             | Keeps the task definitions in `pyproject.toml` alongside the uv/ruff/mypy config, supports task sequences, and is cross-platform (no Make dependency). Replaced the original Makefile (ADR 0002).                                                                                                                                                                                                                                                                                                                                                         |
| Pre-commit framework           | **pre-commit** with `pre-commit-hooks`, Ruff, gitleaks, hadolint, conventional-pre-commit, and local hooks | Runs the same checks locally that CI runs.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Commit convention              | **Conventional Commits** (enforced by `conventional-pre-commit` at `commit-msg`)                           | Required input for the changelog generator.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Changelog / release notes      | **git-cliff**                                                                                              | See §8 for the comparison.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Secrets                        | **SOPS (git source of truth) → AWS Secrets Manager (synced by CI) → External Secrets Operator**            | SOPS-encrypted secrets stay committed in git with path-based `.sops.yaml` rules mirroring `RSS-Engineering/vmware-operations`; only non-sensitive keys stay readable. A CI job (`sync-secrets`) decrypts changed files to AWS Secrets Manager, and ESO materializes the `app-secrets` Secret in-cluster. This removes manual Secrets Manager population and keeps Argo CD away from ciphertext (no KSOPS plugin, no in-cluster decrypt hook). Plain SOPS-applied Secrets were the prior choice; ESO decouples rotation from deploys. See §9 and ADR 0001. |
| GitOps controller              | **Argo CD** (`argoproj.io/v1alpha1`, `kind: Application`)                                                  | Declarative delivery of the rendered overlays; replaces the old CI apply job (was a non-goal). Flux was the main alternative; Argo CD matches the org's direction and pairs cleanly with ESO (nothing to decrypt at sync time). Assumes a cluster-level Argo CD install (not shipped here), like the Datadog Agent. See §10 and ADR 0001.                                                                                                                                                                                                                 |
| CI/CD                          | **GitHub Actions**                                                                                         | Repo already hosted on GitHub.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Container registry             | **GitHub Container Registry (ghcr.io)**                                                                    | Native to GitHub Actions auth via `GITHUB_TOKEN`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Service catalog                | **Backstage** `catalog-info.yml` (kind `Component`) + `mkdocs.yml` for TechDocs                            | Matches the `RSS-Engineering/cloud-environments` convention.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Documentation                  | **MkDocs + Material** in `docs/`, built with `mkdocs build --strict` in CI                                 | TechDocs in Backstage renders MkDocs natively.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Monitoring / error handling    | **Datadog APM (`ddtrace`)** with unified service tagging, log–trace correlation, and runtime metrics       | Rackspace standard observability platform; APM, logs, and metrics correlate through one set of tags. Assumes a cluster-level Datadog Agent DaemonSet (not shipped here). See §5.1 and `docs/monitoring.md`.                                                                                                                                                                                                                                                                                                                                               |
| Dependency updates             | **Dependabot** (`github-actions`, `uv`, `docker`, `pre-commit` ecosystems)                                 | Keeps pinned actions and images current.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Local infrastructure           | **Docker Compose** (Postgres 17, Redis 7)                                                                  | Lets developers and integration tests run against real services.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

---

## 3. Repository layout

```
.
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml              # lint, commits, test, audit, k8s, docs, image
│       ├── release.yml         # on v* tag: image push, changelog, GitHub release
│       ├── security.yml        # scheduled pip-audit + gitleaks + trivy full scan
│       └── sync-secrets.yml    # on secrets.*.yaml change: SOPS -> AWS Secrets Manager (§9, §10)
├── .python-version             # 3.13
├── .sops.yaml                  # SOPS creation rules (KMS)
├── .pre-commit-config.yaml
├── .dockerignore
├── .gitignore
├── Dockerfile
├── README.md
├── SPEC.md                     # this file
├── catalog-info.yml            # Backstage Component
├── cliff.toml                  # git-cliff configuration
├── docker-compose.yml          # local Postgres + Redis
├── mkdocs.yml                  # TechDocs configuration
├── pyproject.toml              # project metadata, deps, ruff, mypy, pytest config
├── uv.lock                     # generated by uv, committed
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_create_items.py
├── src/
│   └── app/
│       ├── __init__.py
│       ├── main.py             # create_app(), lifespan, router registration
│       ├── config.py           # Settings (pydantic-settings)
│       ├── db.py               # async engine + session factory + get_session dependency
│       ├── cache.py            # redis client factory + get_redis dependency
│       ├── models.py           # SQLAlchemy declarative base + Item model
│       ├── schemas.py          # Pydantic request/response models
│       ├── api/
│       │   ├── __init__.py
│       │   ├── health.py       # /healthz (liveness), /readyz (db + redis)
│       │   └── items.py        # sample CRUD resource
│       └── py.typed
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/            # marked `integration`; needs Postgres + Redis
├── deploy/
│   ├── argocd/                             # cluster-scoped, register-once-per-cluster (no namespace: transformer)
│   │   ├── kustomization.yaml
│   │   ├── application-dev.yaml            # Argo CD Application -> overlays/dev (auto-sync)
│   │   ├── application-prod.yaml           # Argo CD Application -> overlays/prod (manual sync)
│   │   └── clustersecretstore.yaml         # ESO ClusterSecretStore (AWS Secrets Manager, IRSA)
│   └── k8s/
│       ├── base/
│       │   ├── kustomization.yaml
│       │   ├── deployment.yaml
│       │   ├── service.yaml
│       │   ├── configmap.yaml
│       │   ├── migration-job.yaml          # Argo CD PreSync hook (migrate before the Deployment rolls out)
│       │   ├── backup-cronjob.yaml         # daily pg_dump -> S3 (SSE-KMS)
│       │   ├── backup-serviceaccount.yaml  # IRSA SA for the backup CronJob (token automounted)
│       │   ├── networkpolicy.yaml
│       │   └── serviceaccount.yaml
│       ├── crd-schemas/                    # vendored CRD JSON schemas for kubeconform (offline)
│       │   ├── Application_v1alpha1.json
│       │   ├── ExternalSecret_v1.json
│       │   ├── ClusterSecretStore_v1.json
│       │   └── README.md
│       └── overlays/
│           ├── dev/
│           │   ├── kustomization.yaml      # patches BACKUP_PREFIX=dev
│           │   ├── externalsecret.yaml     # ESO ExternalSecret -> app-secrets (dev)
│           │   └── secrets.example.yaml    # committed placeholders; secrets.dev.yaml (SOPS) synced to Secrets Manager by CI, never applied directly
│           └── prod/
│               ├── kustomization.yaml      # patches BACKUP_PREFIX=prod
│               ├── pdb.yaml                # PodDisruptionBudget (prod only)
│               ├── externalsecret.yaml     # ESO ExternalSecret -> app-secrets (prod)
│               └── secrets.example.yaml    # committed placeholders; secrets.prod.yaml (SOPS) synced to Secrets Manager by CI, never applied directly
├── docs/
│   ├── index.md
│   ├── getting-started.md
│   ├── architecture.md
│   ├── configuration.md
│   ├── database-migrations.md
│   ├── deployment.md
│   ├── monitoring.md
│   ├── secrets.md
│   ├── security.md
│   ├── releasing.md
│   └── adr/
│       ├── 0001-technology-choices.md      # short ADR pointing to SPEC.md §2
│       └── 0002-task-runner-poe.md         # Make -> poe decision
├── .template/
│   └── placeholders.yml                    # placeholder registry, see §3.1
└── scripts/
    ├── check_sops_encrypted.py             # pre-commit guard, see §9
    └── bootstrap_backups.py                # provision the S3 backup bucket + backup KMS CMK (operator-run)
```

**Cloud infrastructure this layout assumes** (provisioned outside the manifests,
not shipped as running infra by this repo):

- An **Argo CD** install and the **External Secrets Operator** (ESO ≥ v0.10.0),
  both cluster-level, like the Datadog Agent.
- **AWS Secrets Manager** entries `k8s-fastapi-starter/<env>/app-secrets`,
  written by the `sync-secrets` CI job (§9, §10). Default Secrets Manager key —
  no CMK (see §9).
- An **S3 backup bucket** and a **backup-only KMS CMK**, provisioned by
  `scripts/bootstrap_backups.py`, and the **daily backup CronJobs**
  (`deploy/k8s/base/backup-cronjob.yaml`) that `pg_dump` to that bucket.

### 3.1 Template placeholders

This repo is a starter template, so identity values are placeholders rather than
real data. The convention (owner-confirmed):

- **FILL tokens** use `PLACEHOLDER_<NAME>` (always the `PLACEHOLDER_`
  prefix). They are valid plain strings, so the template still builds and passes
  CI before initialization; `rg PLACEHOLDER_` lists every one.
- **RENAME** values are working defaults that must become the new project's
  identity (e.g. `PROJECT_SLUG` = `k8s-fastapi-starter`). They stay valid strings
  (DNS-1123 labels, a valid distribution name), not tokens.
- `.template/placeholders.yml` is the machine-readable registry (fill / rename /
  keep / runtime / external). `AGENTS.md` → "Creating a real repository from this
  template" is the procedure an agent follows to initialize a new service. After
  init, `.template/` is deleted and `rg PLACEHOLDER_` returns nothing.

Notes:

- The app uses the **`src/` layout** so tests import the installed package, not the working tree.
- `deploy/k8s/overlays/*/secrets.*.yaml` are the only secret files that may be committed, and only in SOPS-encrypted form.

---

## 4. Application design

- **Entry point:** `app.main:create_app` is a factory. Uvicorn runs it with `uvicorn app.main:create_app --factory`.
- **Lifespan:** creates the async SQLAlchemy engine and the Redis client on startup, and disposes both on shutdown. Both are stored on `app.state`.
- **Settings:** `config.Settings` reads env vars. Required: `DATABASE_URL` (`postgresql+asyncpg://...`), `REDIS_URL`. Optional: `LOG_LEVEL` (default `INFO`), `APP_ENV` (default `local`).
- **Health endpoints:**
  - `GET /healthz` — liveness. Returns 200 with no dependency checks.
  - `GET /readyz` — readiness. Runs `SELECT 1` on Postgres and `PING` on Redis. Returns 503 if either fails. Used by the Kubernetes readiness probe.
- **Sample resource:** `items` with `id`, `name`, `created_at`. CRUD routes under `/api/v1/items`. Demonstrates the DB session dependency, Redis dependency (cache-aside on `GET /items/{id}`), and migrations.
- **Typing rules:** every function has annotations; `Any` is not allowed unless justified with a comment; SQLAlchemy models use `Mapped[...]`; Pydantic models are the only request/response types.

---

## 5. Configuration

All runtime configuration is environment variables. Kubernetes supplies them from a ConfigMap (non-secret) and a Secret (credentials). Local development reads a `.env` file (git-ignored; `.env.example` is committed).

| Variable       | Required | Source in cluster | Example (placeholder)                                   |
| -------------- | -------- | ----------------- | ------------------------------------------------------- |
| `DATABASE_URL` | yes      | Secret            | `postgresql+asyncpg://app:<password>@postgres:5432/app` |
| `REDIS_URL`    | yes      | Secret            | `redis://:<password>@redis:6379/0`                      |
| `APP_ENV`      | no       | ConfigMap         | `dev` / `prod`                                          |
| `LOG_LEVEL`    | no       | ConfigMap         | `INFO`                                                  |

Never commit real values. Placeholders only in docs and examples.

### 5.1 Datadog (ddtrace)

The app is instrumented with `ddtrace`. The Kubernetes manifests supply the
Datadog env var contract; the app reads it. Full operator notes are in
`docs/monitoring.md`.

| Variable                     | Value                                      | Source in cluster                                                  |
| ---------------------------- | ------------------------------------------ | ------------------------------------------------------------------ |
| `DD_SERVICE`                 | `k8s-fastapi-starter`                      | pod label `tags.datadoghq.com/service` via `fieldRef`              |
| `DD_ENV`                     | `dev` / `prod`                             | pod label `tags.datadoghq.com/env` via `fieldRef` (overlays patch) |
| `DD_VERSION`                 | image tag (placeholder `0.0.0`)            | pod label `tags.datadoghq.com/version` via `fieldRef` (CD patches) |
| `DD_AGENT_HOST`              | node host IP                               | `status.hostIP` via `fieldRef`                                     |
| `DD_TRACE_AGENT_PORT`        | `8126`                                     | ConfigMap `app-config`                                             |
| `DD_TRACE_ENABLED`           | `true` (API); `false` on the migration Job | ConfigMap, overridden on the Job                                   |
| `DD_LOGS_INJECTION`          | `true`                                     | ConfigMap `app-config`                                             |
| `DD_RUNTIME_METRICS_ENABLED` | `true`                                     | ConfigMap `app-config`                                             |
| `DD_PROFILING_ENABLED`       | `false`                                    | ConfigMap `app-config`                                             |

- **Unified service tagging:** the Deployment pod template carries
  `tags.datadoghq.com/{env,service,version}` labels. These are the single source
  of truth — the container reads them back into `DD_ENV`/`DD_SERVICE`/`DD_VERSION`
  with `fieldRef`, and the Datadog Agent tags the pod's logs and metrics from the
  same labels.
- **Non-secret DD config** (port, enabled flags) lives in the ConfigMap;
  `DATABASE_URL`/`REDIS_URL` stay in the SOPS Secret unchanged.
- **Cluster-agent assumption:** this repo does **not** install Datadog. It assumes
  a cluster-level Datadog Agent DaemonSet already runs on every node and holds the
  API key. App pods reach the node-local agent via `DD_AGENT_HOST=status.hostIP`
  on `8126/TCP` (APM) and `8125/UDP` (DogStatsD). No API key or account lives in
  this repo. The optional `admission.datadoghq.com/enabled: "true"` pod annotation
  activates the Datadog Admission Controller when the cluster has one; it is
  harmless otherwise. The base NetworkPolicy allows egress to the agent on
  `8126/TCP` and `8125/UDP`.

---

## 6. Quality and security gates

| Gate                    | Tool                              | Runs in pre-commit | Runs in CI                           | Blocks merge                                                                                                                                                                   |
| ----------------------- | --------------------------------- | ------------------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Format                  | Ruff format                       | yes                | yes                                  | yes                                                                                                                                                                            |
| Lint                    | Ruff check                        | yes                | yes                                  | yes                                                                                                                                                                            |
| Typing                  | mypy --strict                     | yes (local hook)   | yes                                  | yes                                                                                                                                                                            |
| Tests                   | pytest                            | no                 | yes (with Postgres + Redis services) | yes                                                                                                                                                                            |
| Python dependency vulns | pip-audit                         | no                 | yes                                  | yes                                                                                                                                                                            |
| Python code security    | Ruff `S` rules                    | yes                | yes                                  | yes                                                                                                                                                                            |
| Secrets in git          | gitleaks                          | yes                | yes                                  | yes                                                                                                                                                                            |
| Plaintext secret files  | `scripts/check_sops_encrypted.py` | yes                | yes                                  | yes                                                                                                                                                                            |
| Dockerfile lint         | hadolint                          | yes                | yes                                  | yes                                                                                                                                                                            |
| K8s schema              | kubeconform                       | no                 | yes                                  | yes (CRD kinds — Argo CD `Application`, ESO `ExternalSecret`/`ClusterSecretStore` — validated against vendored schemas under `deploy/k8s/crd-schemas/` via `-schema-location`) |
| K8s security            | kube-linter                       | no                 | yes                                  | yes                                                                                                                                                                            |
| Image vulns             | Trivy                             | no                 | yes                                  | yes (CRITICAL/HIGH, unfixed ignored)                                                                                                                                           |
| Docs build              | `mkdocs build --strict`           | no                 | yes                                  | yes                                                                                                                                                                            |
| Commit message          | conventional-pre-commit           | yes (commit-msg)   | yes (PR title/commits check)         | yes                                                                                                                                                                            |

### 6.1 pip-audit

pip-audit audits the exact locked versions, not the local environment:

```bash
uv export --frozen --no-emit-project --no-hashes --format requirements-txt > requirements.audit.txt
uvx pip-audit --requirement requirements.audit.txt --strict --vulnerability-service osv
rm requirements.audit.txt
```

Wrapped as `uv run poe audit`. `--strict` fails on any vulnerability with no fix available as well as on fixable ones.

### 6.2 Ruff security rules

`pyproject.toml` enables the `S` ruleset (bandit equivalents). Test files ignore `S101` (assert). Suppressions must be per-line with a reason, e.g. `# noqa: S608 - static SQL, no user input`.

---

## 7. Database migrations

- Alembic uses the async template: `migrations/env.py` reads `DATABASE_URL` from `app.config.Settings` and runs migrations through an async engine.
- **Create a migration:** `uv run poe migration "add widgets table"` → `uv run alembic revision --autogenerate -m "..."`. Review the generated file; autogenerate misses some changes.
- **Apply locally:** `uv run poe db-upgrade` → `uv run alembic upgrade head`.
- **In Kubernetes:** `deploy/k8s/base/migration-job.yaml` is a Job that runs `alembic upgrade head` using the same image and Secret. It is an **Argo CD PreSync hook** (`sync-wave: "-1"`): Argo CD runs it to completion **before** the main sync rolls out the Deployment, enforcing "migrate before the new pods start" declaratively. A failed migration fails the sync and stops the rollout. The Job's pod template is immutable, but `hook-delete-policy: BeforeHookCreation` deletes the previous hook Job before creating the next, so each sync gets a fresh Job and no per-run unique naming is needed — the old SHA-append scheme (and the removed `deploy.yml` apply workflow) is gone. Overlays still rename the Job per environment (`app-migrate-dev` / `app-migrate-prod`) for identity.
- **Rules:**
  - Migrations must be backward-compatible with the previous app version (expand, then contract) because old and new pods can overlap during rollout.
  - Never edit a migration that has been released. Add a new one.
  - Downgrades are written but not run automatically in production.

---

## 8. Changelog and release notes: git-cliff

**Decision: git-cliff.**

| Option                    | Assessment                                                                                                                                                                                                                                                                     |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **git-cliff** (chosen)    | Single static Rust binary, no Node or Python runtime needed in CI. Configured by one `cliff.toml`. Parses Conventional Commits, groups by type, supports `--bump` to compute the next semver, `--latest` for GitHub release bodies, and custom templates. Actively maintained. |
| generate-changelog (Node) | Works, but needs Node in CI, is less actively maintained, and has less flexible templating.                                                                                                                                                                                    |
| release-please (Google)   | Opens release PRs automatically. Good for teams that want a bot to own versioning, but it adds a bot identity and a PR workflow this template does not need. Revisit if the org standardizes on it.                                                                            |
| conventional-changelog    | Node-based, flexible, but the setup cost is higher than git-cliff for the same output.                                                                                                                                                                                         |

Usage:

- `uv run poe changelog` → `git cliff -o CHANGELOG.md` regenerates the full changelog.
- Release: `git cliff --bump -o CHANGELOG.md`, commit `chore(release): vX.Y.Z`, tag `vX.Y.Z`, push tag. `release.yml` builds and publishes; release notes = `git cliff --latest --strip header`.
- Commit types map to sections: `feat` → Features, `fix` → Bug Fixes, `perf`, `refactor`, `docs`, `build`, `ci`. `chore(release)` and `chore(deps)` are skipped.

---

## 9. Secrets: SOPS

- `.sops.yaml` mirrors the `RSS-Engineering/vmware-operations` pattern:
  - Rules are evaluated in order; first match wins.
  - `path_regex: secrets\.dev\.yaml$` and `secrets\.prod\.yaml$` use an AWS KMS key group (placeholder ARN in the template).
  - `unencrypted_regex: '^(username|description)$'` keeps non-sensitive keys readable in diffs. Adjust per team.
- **Only encrypted files may be committed.** `scripts/check_sops_encrypted.py` runs in pre-commit and CI and fails if any `deploy/**/secrets.*.yaml` lacks a top-level `sops:` block.
- Plaintext examples live in `deploy/k8s/overlays/*/secrets.example.yaml` (placeholders only, not matched by the guard, never applied).
- Create or edit: `sops deploy/k8s/overlays/dev/secrets.dev.yaml`.

### 9.1 SOPS → AWS Secrets Manager → External Secrets Operator

SOPS stays the **git source of truth**. The in-cluster path is no longer a CD `kubectl apply` of a decrypted Secret; it is:

1. **CI sync.** The `sync-secrets` workflow (§10) triggers when a `secrets.*.yaml` file changes. It decrypts the file with SOPS and writes one JSON entry per environment — `k8s-fastapi-starter/<env>/app-secrets`, keys `DATABASE_URL` and `REDIS_URL` — to **AWS Secrets Manager** (`put-secret-value`, creating the secret on first run). Rotation is therefore `sops <file>` → commit; CI does the rest, with no manual `aws secretsmanager` call for the normal case.
2. **ESO materialization.** A per-overlay `ExternalSecret` (`deploy/k8s/overlays/<env>/externalsecret.yaml`) points at that Secrets Manager entry via the cluster-scoped `ClusterSecretStore` `aws-secrets-manager` (`deploy/argocd/clustersecretstore.yaml`). ESO reads the entry and materializes the `app-secrets` Kubernetes Secret that `deployment.yaml`/`migration-job.yaml` consume unchanged. Argo CD never sees ciphertext — there is no KSOPS plugin and no in-cluster decrypt hook.

- **Key isolation without a secrets CMK.** The **default** (AWS-managed) Secrets Manager key is intentional. Two distinct IAM roles provide the isolation a CMK key policy otherwise would: the CI OIDC role is **write-only** (`secretsmanager:PutSecretValue`/`CreateSecret`/`DescribeSecret`), and ESO's IRSA role is **read-only** (`secretsmanager:GetSecretValue`/`DescribeSecret`). The **only** CMK this design provisions is for the S3 **backup** bucket.
- **Break-glass is transient.** `sops --decrypt … | kubectl apply -f -` still works, but ESO's `refreshInterval` (default 1h) will overwrite the Secret with the Secrets Manager value. Real break-glass is pushing to Secrets Manager (or editing the `ExternalSecret`), not kubectl-applying a decrypted Secret. See `docs/secrets.md`.

---

## 10. CI/CD (GitHub Actions)

**`ci.yml`** — on pull requests and pushes to `main`:

1. `lint`: pre-commit `run --all-files`, mypy, ruff.
2. `test`: service containers for Postgres 17 and Redis 7; `uv run poe db-upgrade` then `uv run poe test`.
3. `audit`: `uv run poe audit` (pip-audit).
4. `k8s`: `kubectl kustomize` for dev and prod, kubeconform, kube-linter.
5. `docs`: `mkdocs build --strict`.
6. `image`: build the Docker image, Trivy scan (fail on CRITICAL/HIGH with fix available), push to GHCR on `main` only, tagged with commit SHA.

**`release.yml`** — on tag `v*`: build and push image tagged with the version, generate release notes with git-cliff, create the GitHub release.

**`security.yml`** — weekly and on demand: pip-audit, gitleaks over full history, Trivy on the latest `main` image.

**`sync-secrets.yml`** — a standalone, path-filtered workflow (triggers only on `deploy/k8s/overlays/**/secrets.*.yaml` changes, so it is separate from `ci.yml`). On push to `main` it assumes the AWS OIDC role (`vars.AWS_DEPLOY_ROLE_ARN` in `vars.AWS_REGION`), decrypts each changed `secrets.<env>.yaml` with SOPS, validates the key set is exactly `{DATABASE_URL, REDIS_URL}`, and writes it to AWS Secrets Manager (`put-secret-value`, `create-secret` on first run). Fork PRs run only a dry-run key-shape check (no AWS). The OIDC role needs `secretsmanager:PutSecretValue`, `secretsmanager:CreateSecret`, and `secretsmanager:DescribeSecret` scoped to `arn:aws:secretsmanager:*:*:secret:k8s-fastapi-starter/*/app-secrets-*`, in addition to the `kms:Decrypt` it already has for SOPS (§9.1, §13 item 5).

**Deployment:** **Argo CD** applies the rendered overlays (GitOps) — `deploy/argocd/` holds an `Application` per environment plus the ESO `ClusterSecretStore`. The migration-Job-before-Deployment ordering is expressed as an **Argo CD PreSync hook** on the migration Job (not an imperative CI wait). The old manual `deploy.yml` apply workflow has been **removed**; break-glass manual apply is documented in `docs/deployment.md`. The cluster target and how each `Application` is registered are per-platform; see §13.

**Datadog at deploy:** the deploy step must set the `tags.datadoghq.com/version` (and `app.kubernetes.io/version`) pod-template label to the deployed image tag at render time, alongside `kustomize edit set image`, so `DD_VERSION` matches the artifact. Until that is wired it stays the placeholder `0.0.0`. The migration Job runs with `DD_TRACE_ENABLED=false` (no APM for a short-lived job). The cluster-level Datadog Agent DaemonSet is assumed to exist and is not part of this repo (§5.1).

Actions must be pinned to a major version at minimum. Pinning to full SHAs is recommended for security workflows.

---

## 11. Documentation and Backstage

- `catalog-info.yml` defines a `Component` with `type: service`, `lifecycle: experimental`, `owner` placeholder, and `backstage.io/techdocs-ref: dir:.`. Annotate with `github.com/project-slug`.
- `mkdocs.yml` at the root uses `docs_dir: docs` and the Material theme. TechDocs builds it from the repo root.
- `docs/` holds the operator and developer docs. The ADR index is `docs/adr/`. Any change to a decision in SPEC.md §2 requires a new ADR.

---

## 12. Task runner — poethepoet (the documented interface)

Tasks are defined in `pyproject.toml` under `[tool.poe.tasks]` and run with
`uv run poe <task>`. `uv run poe` with no task lists them all. poe runs inside
the uv environment, so `ruff`/`mypy`/`pytest`/`alembic`/`pre-commit` are on PATH
without a `uv run` prefix. See ADR `docs/adr/0002-task-runner-poe.md` for why poe
replaced the original Makefile.

| Task                                | Purpose                                                             |
| ----------------------------------- | ------------------------------------------------------------------- |
| `uv run poe install`                | `uv sync --all-groups`                                              |
| `uv run poe run`                    | `uvicorn app.main:create_app --factory --reload`                    |
| `uv run poe up` / `uv run poe down` | Docker Compose Postgres + Redis                                     |
| `uv run poe lint`                   | `ruff check` + `ruff format --check`                                |
| `uv run poe format`                 | `ruff format` + `ruff check --fix`                                  |
| `uv run poe typecheck`              | `mypy`                                                              |
| `uv run poe test`                   | `pytest`                                                            |
| `uv run poe audit`                  | pip-audit (§6.1)                                                    |
| `uv run poe security`               | gitleaks + ruff S + sops guard                                      |
| `uv run poe k8s-check`              | kustomize build + kubeconform + kube-linter                         |
| `uv run poe docs` / `docs-build`    | `mkdocs serve` / `mkdocs build --strict`                            |
| `uv run poe db-upgrade`             | `alembic upgrade head`                                              |
| `uv run poe migration "..."`        | autogenerate a migration (positional message)                       |
| `uv run poe changelog`              | `git cliff -o CHANGELOG.md`                                         |
| `uv run poe hooks`                  | `pre-commit install --install-hooks` and `commit-msg`               |
| `uv run poe check`                  | lint + typecheck + test (pre-PR loop)                               |
| `uv run poe check-all`              | lint + typecheck + audit + security + test + k8s-check + docs-build |

---

## 13. Open questions (not yet decided)

These are intentionally left for the repository owner:

1. **Agent instructions:** Should we add `AGENTS.md` (and `CLAUDE.md` pointing to it)? What belongs in it beyond §14?
2. **Claude skills:** Should the repo ship project-level skills in `.claude/skills/` (for example "add an endpoint", "add a migration", "release")?
3. **Deploy target:** *Decided — GitOps via Argo CD, not a CI apply job.* See ADR 0001 and §10; `deploy/argocd/` holds an `Application` per environment. Which specific cluster(s)/namespaces each `Application` targets and how it is registered remain per-platform details for the repository owner.
4. **Secrets runtime:** *Decided — External Secrets Operator with AWS Secrets Manager, fed by SOPS via CI.* See ADR 0001 and §9.1. SOPS stays the git source of truth; ESO (≥ v0.10.0) materializes `app-secrets` in-cluster.
5. **AWS KMS ARNs and CI auth:** Which account/role does CI assume for SOPS decryption? (Placeholder in template.) The CI OIDC role now **additionally** needs `secretsmanager:PutSecretValue`, `secretsmanager:CreateSecret`, and `secretsmanager:DescribeSecret` scoped to `arn:aws:secretsmanager:*:*:secret:k8s-fastapi-starter/*/app-secrets-*`, on top of the existing `kms:Decrypt` for SOPS — see §9.1, §10, and `.github/workflows/sync-secrets.yml`.
6. **Backstage owner:** Which group owns this component (`group:default/...`)? Which system does it belong to?
7. **Ingress and TLS:** Is there a standard ingress controller or gateway, and is it in scope for the template?
8. **Auth:** Does the API need authentication (OIDC/JWT) in the template, or is that per-service?
9. **Observability:** *Decided — Datadog APM (`ddtrace`) with unified service tagging, log–trace correlation, and runtime metrics.* See §2, §5.1, and `docs/monitoring.md`. (Prometheus/OpenTelemetry are not used; revisit only if the platform standard changes.)
10. **Image registry:** GHCR (chosen as default) or the organization's registry?

---

## 14. Instructions for later agents

Read this section before changing the repo.

1. **Read SPEC.md first.** Do not change a choice in §2 without asking the repository owner, and record any agreed change in `docs/adr/` and in this file.
2. **Use the poe tasks (§12) rather than raw tool invocations** so local runs match CI.
3. **Use `uv` for everything Python.** Add dependencies with `uv add` (and `uv add --dev`). Never edit `uv.lock` by hand. Never use `pip install` in this repo.
4. **Keep `.python-version` as the single source of Python version.** The Dockerfile and CI read it.
5. **Typing:** new code must pass `mypy --strict`. Do not add `# type: ignore` without an error code and a reason.
6. **Commits:** use Conventional Commits (`feat(items): add delete endpoint`). The changelog depends on it.
7. **Migrations:** create with `uv run poe migration`, review the output, keep them backward-compatible (§7). Never edit a released migration.
8. **Secrets:** never commit plaintext secrets. Use `sops` for `deploy/**/secrets.*.yaml`. Examples use placeholders only. If a guard or gitleaks fires, fix the cause; do not add it to an allowlist without asking.
9. **Security suppressions:** `# noqa: S...` and kube-linter ignores need a written reason on the same line or in the ignore file.
10. **Tests:** unit tests must run with no services. Tests needing Postgres or Redis are marked `@pytest.mark.integration` and run in CI.
11. **Docs:** update `docs/` with any user-visible behavior change. `mkdocs build --strict` must pass.
12. **Do not** add infrastructure that is not in §3 without updating this spec.
13. **Do not** upload repo or customer data to external AI or third-party services not on the Rackspace approved list.
14. **Placeholders:** values a new project must supply are `PLACEHOLDER_<NAME>` tokens (see §3.1 and `.template/placeholders.yml`). When adding a new such value, use that format, register it, and keep it a valid string so the template still builds. Never fabricate real values (orgs, ARNs, emails) to fill a token.

---

## 15. Acceptance criteria for the template

- `uv run poe install && uv run poe up && uv run poe db-upgrade && uv run poe test` succeeds on a clean clone with Docker and uv installed.
- `uv run poe check-all` passes.
- `pre-commit run --all-files` passes.
- `docker build .` produces an image that runs as a non-root user and serves `/healthz`.
- `kubectl kustomize deploy/k8s/overlays/dev` and `.../prod` render without error.
- CI workflows are valid YAML and pass `actionlint` (if available).
- `mkdocs build --strict` passes.
- `catalog-info.yml` validates against Backstage's Component schema (required fields present).
