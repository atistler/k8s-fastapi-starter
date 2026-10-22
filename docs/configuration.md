# Configuration

All runtime configuration comes from environment variables. In the cluster, Kubernetes supplies them from a
ConfigMap (non-secret values) and a Secret (credentials). Locally, a git-ignored `.env` file is read.
`.env.example` is committed and holds placeholders only.

## Variables

| Variable | Required | Default | Source in cluster | Example (placeholder) |
|---|---|---|---|---|
| `DATABASE_URL` | yes | none | Secret | `postgresql+asyncpg://app:<password>@postgres:5432/app` |
| `REDIS_URL` | yes | none | Secret | `redis://:<password>@redis:6379/0` |
| `APP_ENV` | no | `local` | ConfigMap | `dev` or `prod` |
| `LOG_LEVEL` | no | `INFO` | ConfigMap | `INFO` |

`DATABASE_URL` must use the `postgresql+asyncpg://` scheme. The app uses the async SQLAlchemy engine.

### Datadog (`DD_*`)

Datadog is configured through `DD_*` environment variables read directly by `ddtrace` (via `ddtrace-run`)
and the log formatter — they are **not** mirrored as `app.config.Settings` fields. In the cluster the
Deployment sets them (service/env/version from pod labels, `DD_AGENT_HOST` from the node IP); locally they
default to tracing off. See [Monitoring](monitoring.md) and SPEC §5.1 for the full contract.

## Local values

Create a `.env` file from the example and replace the placeholders:

```bash
cp .env.example .env
```

Local Compose services listen on `localhost:5432` (Postgres) and `localhost:6379` (Redis). Use the
credentials from `docker-compose.yml` for local development only. Never reuse them in a cluster.

## Rules

- Never commit real values. Use placeholders in docs, examples, and tests.
- Anything secret goes in the SOPS-encrypted Secret, not the ConfigMap. See [Secrets](secrets.md).
- Adding a variable means updating `app.config.Settings`, this page, and the ConfigMap or Secret in `deploy/k8s/`.
