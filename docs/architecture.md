# Architecture

## Components

```text
            +-----------------+
 client --> |  FastAPI (uvicorn)  |--- SQL (asyncpg) ---> PostgreSQL 17
            |  app.main       |
            |                 |--- redis.asyncio ---> Redis 7
            +-----------------+
                     ^
                     | migration Job runs first (alembic upgrade head)
```

- **Application factory.** `app.main:create_app` builds the app. Uvicorn runs it with
  `uvicorn app.main:create_app --factory`.
- **Lifespan.** Startup creates the async SQLAlchemy engine and the Redis client and stores them on
  `app.state`. Shutdown disposes both.
- **Settings.** `app.config.Settings` (pydantic-settings) reads environment variables. See [Configuration](configuration.md).
- **Database.** `app.db` holds the engine, the session factory, and the `get_session` dependency.
  Models use SQLAlchemy 2.x `Mapped[...]` annotations in `app.models`.
- **Cache.** `app.cache` holds the Redis client factory and the `get_redis` dependency.
- **Schemas.** Pydantic models in `app.schemas` are the only request and response types.

## Layout

```text
src/app/
  main.py        create_app(), lifespan, router registration
  config.py      Settings
  db.py          async engine, session factory, get_session
  cache.py       Redis client, get_redis
  models.py      declarative base and Item model
  schemas.py     request and response models
  api/
    health.py    /healthz, /readyz
    items.py     /api/v1/items CRUD
migrations/      Alembic environment and versions
deploy/k8s/      Kustomize base and overlays
```

## Endpoints

| Method and path | Purpose                                | Dependencies checked                                         |
| --------------- | -------------------------------------- | ------------------------------------------------------------ |
| `GET /healthz`  | Liveness                               | None                                                         |
| `GET /readyz`   | Readiness (Kubernetes readiness probe) | `SELECT 1` on Postgres, `PING` on Redis; 503 if either fails |
| `/api/v1/items` | Sample CRUD resource                   | Postgres; Redis for cache-aside on `GET /items/{id}`         |

## Request flow for `GET /api/v1/items/{id}`

1. The handler asks Redis for the cached item.
2. On a miss, it loads the row through the session from `get_session`.
3. It stores the result in Redis and returns the Pydantic response model.

The sample exists only to prove the wiring. Business logic belongs in your service.

## Kubernetes shape

The base defines a Deployment, Service, ConfigMap, ServiceAccount, NetworkPolicy, a migration Job, and a daily database
backup CronJob (with its own IRSA ServiceAccount). Overlays for `dev` and `prod` patch names, image, replicas, and the
backup S3 prefix.

Delivery and secrets are GitOps:

- **Argo CD** applies the rendered overlays (`deploy/argocd/` holds an `Application` per environment plus the ESO
  `ClusterSecretStore`). The migration Job runs first as an Argo CD PreSync hook, before the Deployment rolls out.
- **External Secrets Operator (ESO)** materializes the `app-secrets` Secret from AWS Secrets Manager (fed by SOPS via
  the `sync-secrets` CI job), so no decrypted secret is applied by CD.
- The **backup CronJob** streams `pg_dump` to an S3 bucket (SSE-KMS) once a day.

Argo CD and ESO are assumed pre-installed cluster-level, like the Datadog Agent. See [Deployment](deployment.md) and
[Secrets](secrets.md).
