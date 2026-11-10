# Design: In-cluster HA Postgres (CloudNativePG) + Redis Sentinel

## Overview

This is the **third** batch in a series. Two prior batches already landed on
`main` and this design builds on them, undoing none of it:

1. **Argo CD GitOps + SOPS → AWS Secrets Manager → External Secrets Operator
   (ESO)** for secret delivery (`deploy/argocd/`, per-overlay `ExternalSecret`,
   `ClusterSecretStore`, `sync-secrets.yml`, vendored CRD schemas, the two-pass
   `k8s-check` gate).
2. A **`PROJECT_SLUG` template-placeholder coverage expansion** (the
   `rename.PROJECT_SLUG.replace_in` list in `.template/placeholders.yml`).

This batch reverses the standing SPEC §2 non-goal — *"A specific cloud
provider's managed database or cache. Postgres and Redis are reached by URL from
config"* — and adopts **in-cluster HA Postgres and Redis** as the deployment
target. The three SETTLED inputs (not re-litigated here) are:

- **Postgres = CloudNativePG (CNPG)**: a 3-instance `Cluster` CR (primary + 2
  replicas) with automated failover, quorum-based synchronous replication, a
  PodDisruptionBudget, cross-node anti-affinity, resource requests/limits, and
  CNPG-native scheduled backups to S3 via Barman. The CNPG **operator** is
  assumed pre-installed cluster-side (same stance as ESO / the Datadog Agent);
  this repo ships the `Cluster` / `ScheduledBackup` CRs, not the operator.
- **Redis = Sentinel-mode HA** (NOT Redis Cluster): 1 master + 2 replicas + 3
  sentinels. The Redis operator/chart is assumed pre-installed cluster-side;
  this repo ships the HA custom resource. This **changes application code** (see
  §D below) because the single-`REDIS_URL` model does not survive Sentinel.
- **SPEC reversal recorded in place**: ADR 0001 gets a dated reversal note (no
  new ADR file), consistent with how the Argo CD/ESO reversal was recorded.

Local development stays on Docker Compose with plain Postgres 17 + plain Redis 7
— **unchanged**. The app's HA awareness is entirely in *connection
construction*: cache-aside and the `PING` readiness check behave identically.

### Locked technology stack (do not change during implementation)

- **In-cluster Postgres: CloudNativePG (CNPG)**, `apiVersion
  postgresql.cnpg.io/v1`, kinds `Cluster`, `ScheduledBackup` (and `Backup` for
  the vendored schema / ad-hoc restores). **Operator version floor: 1.24**
  (current stable LTS line; `postgresql.cnpg.io/v1` has been the stored,
  served API version since well before 1.24, so the floor is comfortably
  satisfied). Documented in `docs/deployment.md` and `SPEC.md`.
- **In-cluster Redis: Sentinel-mode HA via the Spotahome Redis Operator**
  (`apiVersion databases.spotahome.com/v1`, kind `RedisFailover`). This is the
  most widely-used Sentinel-mode operator and models "N redis + M sentinels"
  as a single CR, which is exactly the shape the task calls for. (Alternative
  considered: the Bitnami `redis` Helm chart in `architecture: replication`
  mode with `sentinel.enabled: true`. Rejected as the *shipped* resource because
  it is a Helm release, not a plain kustomize-able CR, and would not fit the
  existing `deploy/k8s/overlays/*` + Argo CD Application structure without an
  Argo CD Helm source — a larger change than a single CR. The operator CR drops
  straight into `resources:`.)
- **Redis client: `redis.asyncio.sentinel.Sentinel(...).master_for(...)`** for
  the Sentinel path; `redis.asyncio.Redis.from_url(...)` unchanged for local
  dev. redis-py is already the chosen client (SPEC §2) and ships `Sentinel`.
- **Backups: CNPG-native `ScheduledBackup` + Barman object store → the SAME S3
  bucket + CMK** that `scripts/bootstrap_backups.py` provisions. The old
  `pg_dump` CronJob is removed.
- **Credential source of truth for Postgres: CNPG-generated Secret.** CNPG
  creates the app-role Secret `pg-app` (superuser access is disabled, so no
  superuser Secret is generated — §A); `app-secrets`' `DATABASE_URL` stops
  flowing through SOPS→Secrets-Manager→ESO and is instead composed in the app
  from the discrete `pg-app` keys (details in §F). The **Redis Sentinel password**
  continues via SOPS→Secrets-Manager→ESO (consumed by the operator CR *and* the
  app).
- CRD validation stays **vendored JSON schemas** under `deploy/k8s/crd-schemas/`,
  canonical-kind-cased filenames (pinned kubeconform 0.8.0 has no `toLower`),
  fed to the existing two-pass `k8s-check` gate.
- Everything else (uv, FastAPI, SQLAlchemy/asyncpg, Alembic, Argo CD, ESO,
  kustomize, kube-linter, poe, GitHub Actions, Datadog) is unchanged.

---

## Current state (verified against the repo)

- `src/app/config.py` — `Settings(BaseSettings)` with **required** `database_url:
  str` and `redis_url: str`, plus optional `app_env`/`log_level`. `env_file=".env"`.
- `src/app/cache.py` — `create_redis(redis_url) -> Redis` via
  `Redis.from_url(redis_url, decode_responses=True)`; `get_redis` returns
  `request.app.state.redis`; `RedisDep` alias.
- `src/app/main.py` — lifespan does `redis = create_redis(resolved.redis_url)`,
  stores it on `app.state.redis`, `await redis.aclose()` on shutdown.
- `migrations/env.py` — `get_url() -> str` returns `Settings().database_url`
  (verified in the repo). Alembic's offline + async-online paths both call
  `get_url()`; the migration Job runs `alembic upgrade head` through this module.
  Because this design makes `database_url` optional and moves the in-cluster DSN
  to `database_dsn`, this call site **must** change too (§F; review finding 1).
- `src/app/api/health.py` — `_check_redis` calls `request.app.state.redis.ping()`.
- `src/app/api/items.py` — cache-aside using `redis.get/set/delete` via `RedisDep`.
- `tests/unit/test_config.py` — asserts `settings.redis_url == "redis://…"`.
- `tests/conftest.py` — `placeholder_env` sets `DATABASE_URL`/`REDIS_URL`.
- `tests/unit/test_health.py` — `/readyz` returns 503 when the lifespan did not
  run (engine/redis absent), i.e. it does not need a live Redis.
- `tests/integration/test_items_api.py` — runs the real lifespan against live
  services; `@pytest.mark.integration`.
- `.env.example` — `DATABASE_URL=…`, `REDIS_URL=…`, plus DD_* vars.
- `docker-compose.yml` — plain `postgres:17` + plain `redis:7` with
  `--requirepass local-only-password`.
- `deploy/k8s/base/` — `serviceaccount`, `configmap`, `deployment`, `service`,
  `networkpolicy`, `migration-job`, `backup-serviceaccount`, `backup-cronjob`
  (listed in that order in `kustomization.yaml`).
- `deploy/k8s/base/backup-cronjob.yaml` — `pg_dump "$DATABASE_URL" | gzip | aws
  s3 cp - s3://…/${BACKUP_PREFIX}/db/<ts>.sql.gz --sse aws:kms`; labels carry
  `app.kubernetes.io/name` (NetworkPolicy selection); `serviceAccountName:
  backup`; `BACKUP_PREFIX` patched per overlay.
- `deploy/k8s/base/backup-serviceaccount.yaml` — SA `backup`,
  `automountServiceAccountToken: true`, IRSA annotation `PLACEHOLDER_BACKUP_ROLE_ARN`.
- `deploy/k8s/base/networkpolicy.yaml` — `NetworkPolicy app`, podSelector
  `app.kubernetes.io/name: <slug>`; egress rule 1 = DNS to kube-system; egress
  rule 2 = any-IP on 5432/6379/8126(TCP)/8125(UDP)/443(TCP).
- `deploy/k8s/overlays/{dev,prod}/kustomization.yaml` — set namespace, image,
  replicas, patch ConfigMap, patch `db-backup` `BACKUP_PREFIX`. Prod adds
  `pdb.yaml` (app PDB, `minAvailable: 2`) and anti-affinity.
- `deploy/k8s/overlays/*/externalsecret.yaml` — ESO `ExternalSecret`
  `app-secrets-external`, `target.name: app-secrets`, `dataFrom[0].extract.key:
  <slug>/<env>/app-secrets`.
- `deploy/k8s/overlays/*/secrets.example.yaml` — `stringData` `DATABASE_URL` +
  `REDIS_URL` with `PLACEHOLDER_DB_PASSWORD` / `PLACEHOLDER_REDIS_PASSWORD`.
- `deploy/k8s/crd-schemas/` — `Application_v1alpha1.json`, `ExternalSecret_v1.json`,
  `ClusterSecretStore_v1.json`, `README.md`.
- `pyproject.toml` `k8s-check` — renders `deploy/k8s/overlays/{dev,prod}` +
  `deploy/argocd`; two kubeconform passes (pass (b) has `-ignore-missing-schemas`
  removed, `-schema-location` only the vendored dir, core kinds `-skip`ped);
  kube-linter on dev/prod only.
- `scripts/bootstrap_backups.py` — provisions S3 bucket + backup CMK + alias;
  `--dry-run` default, `--apply` guarded against `PLACEHOLDER_` values;
  `--retention-days` default 30; lifecycle rule `Filter.Prefix: ""`.
- `.template/placeholders.yml` — fill/rename/keep/runtime/external;
  `PLACEHOLDER_BACKUP_{BUCKET,IMAGE,KMS_KEY_ID,KMS_ALIAS,ROLE_ARN}`;
  `PROJECT_SLUG.replace_in` globs `deploy/k8s/**`, `deploy/argocd/**`,
  `.github/workflows/sync-secrets.yml`, several `src`/`docs`/`tests` files.
- `docs/adr/0001-technology-choices.md` — "Accepted"; a 2026-10-21 dated reversal
  note already records the Argo CD/ESO change in place.

---

## A. Postgres: the CloudNativePG `Cluster`

### Decisions

**Why CNPG `Cluster` with 3 instances and quorum-based synchronous replication.**
CNPG models a Postgres HA cluster as one `Cluster` CR; the operator owns the
primary/replica pods, streaming replication, automated failover (promotes a
standby on primary loss), and the managed Services it publishes. Three instances
(one primary, two hot standbys) is the smallest topology that survives one node
loss *and* keeps a synchronous quorum. Replication is configured
**quorum-based synchronous** so a commit is acknowledged only once at least one
standby has it, without pinning to a single named standby:

```yaml
spec:
  instances: 3
  postgresql:
    synchronous:
      method: any
      number: 1            # ANY 1 of the two standbys must confirm
```

`method: any` + `number: 1` is CNPG's quorum form (preferred over the older
`minSyncReplicas`/`maxSyncReplicas` knobs, which CNPG now treats as legacy). It
tolerates one standby being down (the surviving standby satisfies the quorum)
while still guaranteeing one synchronous copy, so a failover never loses an
acknowledged commit. **Chosen: quorum `any 1`**, documented inline.

**apiVersion floor.** `postgresql.cnpg.io/v1` is the served/stored version on
the 1.24+ line (and has been since long before it). The vendored schema is
`Cluster_v1.json`; the documented operator floor is **CNPG 1.24+** in
`docs/deployment.md` and `SPEC.md`. These three must agree (same discipline as
the ESO v0.10.0 / `external-secrets.io/v1` floor from batch 1).

**Storage.** `spec.storage.size` and `spec.storage.storageClass` are
template-fill values (`PLACEHOLDER_DB_STORAGE_SIZE`,
`PLACEHOLDER_DB_STORAGE_CLASS`) so a consumer picks the PVC size/class for their
platform. A separate WAL volume (`spec.walStorage`) is set to the same
placeholders' WAL variants is **not** added — a single data volume is the CNPG
default and keeps the template minimal; documented as a tuning point.

**Security posture.** CNPG pods already run non-root with a restricted
`securityContext` by default; the CR does not need to restate it. The one
explicit security choice is `spec.enableSuperuserAccess: false` so no `postgres`
superuser password Secret is generated — the app connects as the unprivileged
app role only. This is set **explicitly** rather than relying on it being the
default: CNPG has defaulted this to `false` on recent lines, but the exact
default has varied across the 1.x history, so the manifest states it outright to
be version-independent (verify the value renders as expected on the shipped
1.24+ line). Documented inline.

**Anti-affinity + PDB.** `spec.affinity.enablePodAntiAffinity: true` with
`topologyKey: kubernetes.io/hostname` and `podAntiAffinityType: preferred`
spreads the three instances across nodes without making a small cluster
unschedulable (mirrors the app Deployment's `preferred` anti-affinity in the
prod overlay). CNPG creates and manages its **own** PDB for the cluster
automatically, so this repo does **not** ship a separate Postgres PDB — adding
one would fight the operator. (The existing `overlays/prod/pdb.yaml` is the
*app* PDB and is untouched.) Documented inline in the `Cluster` CR.

**Resources.** `spec.resources.requests/limits` (cpu/memory) are set to concrete
starter values in base (e.g. requests 250m/256Mi, limits "1"/1Gi) and the prod
overlay patches them upward, matching how the app Deployment is sized per
overlay.

**Bootstrap / database + role.** `spec.bootstrap.initdb` creates database `app`
owned by role `app`. **`spec.bootstrap.initdb.secret` is deliberately NOT set**
(review finding 5): omitting it is what makes CNPG *generate* the app-role
credential Secret `pg-app` (`<cluster>-app`) rather than consume a user-supplied
one. CNPG therefore owns the credential, and §F sources `DATABASE_URL` from that
generated Secret's discrete keys. The generated key set
(`username`/`password`/`host`/`port`/`dbname`/`uri`/…) is confirmed against the
shipped CNPG 1.24+ line at implementation; §F only depends on the stable
discrete keys, not on `uri`.

### Services CNPG publishes

CNPG creates three Services per cluster: `<cluster>-rw` (primary, read-write),
`<cluster>-ro` (replicas, read-only), `<cluster>-r` (any instance). The cluster
is named **`pg`** (short, so the Services are `pg-rw`/`pg-ro`/`pg-r`). The app,
the migration Job, and CNPG backups all target **`pg-rw`** (writes + migrations
must hit the primary). `DATABASE_URL`'s host becomes `pg-rw.<namespace>.svc`
(resolved in §F).

### Edge cases / rough edges

- **First-boot ordering (documented, not engineered).** Per batch 1's stance, we
  do NOT add Argo CD sync-wave gymnastics around operator CRD health. On a
  brand-new environment the CNPG `Cluster` takes time to become primary-ready;
  the migration Job PreSync hook (wave `-1`) may run before the primary is
  accepting connections and fail, Argo CD retries the sync, and it succeeds once
  CNPG settles. This is a **one-time, manual-attention event** per new
  environment — documented in `docs/deployment.md` right next to the existing
  ESO first-sync rough edge, not worked around.
- **Replica count vs. quorum.** With `instances: 3` and `any 1`, losing two
  instances at once blocks synchronous commit; documented as the accepted HA
  envelope (survives one node, not two simultaneous).

---

## B. Redis: Sentinel-mode HA via `RedisFailover`

### Decisions

**Why the Spotahome `RedisFailover` CR.** It expresses the exact topology the
task fixes — N redis (1 master + replicas) and M sentinels — in one namespaced
CR the operator reconciles into a StatefulSet of redis pods, a Deployment of
sentinels, and the Services the client talks to. It is the de-facto
Sentinel-mode operator and fits `resources:` with no Helm source. The shipped CR:

```yaml
apiVersion: databases.spotahome.com/v1
kind: RedisFailover
metadata:
  name: redis
spec:
  sentinel:
    replicas: 3
  redis:
    replicas: 3          # 1 master + 2 replicas, master elected by Sentinel
    resources: { requests: {...}, limits: {...} }
  auth:
    secretPath: app-secrets   # Secret MUST hold key `password` (operator convention); see §F
```

- **3 sentinels** so a Sentinel quorum (2) survives one sentinel loss.
- **3 redis** (1 master + 2 replicas) so a failover has a standby to promote.
- **`auth.secretPath: app-secrets`** points the operator at the same
  ESO-materialized `app-secrets` Secret. **VERIFIED key name (review finding 1):
  the Spotahome `redis-operator` reads a key literally named `password` from the
  Secret named in `spec.auth.secretPath` — NOT `REDIS_PASSWORD`.** (Source: the
  operator's `RedisFailover` CRD / docs: `auth.secretPath` references a Secret
  whose `password` key is the Redis `requirepass`/Sentinel `auth-pass`.) The
  earlier claim that the operator reads `REDIS_PASSWORD` was wrong and is
  corrected here. The single-Secret, no-drift property is preserved by having
  ESO materialize **both** keys from the one Secrets Manager value — `password`
  (what the operator consumes) and `REDIS_PASSWORD` (what the app's
  `pydantic-settings` reads) — so both are the same string (see §F for the
  `ExternalSecret` template). This is load-bearing: if only one key existed, the
  operator and the app would authenticate with mismatched credentials.
- Security context and resource limits are set on `spec.redis` /
  `spec.sentinel` so the rendered pods satisfy kube-linter. **This is NOT
  pre-decided clean:** kube-linter runs against the dev/prod renders *with the
  RedisFailover present* during implementation; because the operator (not this
  manifest) generates the final pods, kube-linter only sees the CR itself (an
  unknown kind it does not deep-lint), so the likely outcome is zero new
  diagnostics — but that is *verified*, not assumed, and any check that fires
  gets an exclusion in `deploy/k8s/.kube-linter.yaml` with a written reason per
  SPEC §14.9.

**Services the operator publishes.** The operator creates a `rfr-redis` Service
(redis pods) and a `rfs-redis` Service (sentinels). The app connects to the
**sentinels** (`rfs-redis`) and asks them for the current master; it must NOT
hardcode the master pod. The Sentinel master group name is **`mymaster`** (the
operator's default) — captured in config as `REDIS_MASTER_NAME`.

**CRD schema vendor.** `RedisFailover_v1.json` is vendored under
`deploy/k8s/crd-schemas/` (sourced from the datreeio catalog
`databases.spotahome.com/redisfailover_v1.json`, renamed to canonical kind
casing). Added to both kubeconform passes (see §4).

### Edge cases

- **Master can move.** Sentinel promotes a replica on master loss; the client
  rediscovers via `master_for`, so a brief write error window during failover is
  expected and acceptable (same envelope as CNPG failover). `/readyz` will blip
  503 during the window, which is correct.
- **Password rotation** does not restart running app pods (env read at start);
  same documented caveat as before (Reloader would close it later).

---

## C. SPEC / ADR reversal (edits planned, not made in this step)

- **ADR 0001** — edit in place (NO new ADR). Append a **dated reversal note**:
  *"2026-… : managed-DB/cache non-goal reversed — in-cluster HA adopted:
  CloudNativePG (3-instance `Cluster`, quorum sync, CNPG-native Barman backups)
  and Redis Sentinel (`RedisFailover`, 3 sentinels / 1 master + 2 replicas).
  Local dev stays Docker Compose. Updated in place per the amended policy."* Add
  CNPG + Redis Sentinel to the Decision summary bullet about SQLAlchemy/Redis.
- **SPEC §1/§2** — the non-goal *"A specific cloud provider's managed database or
  cache… reached by URL from config"* flips to: **in-cluster CNPG Cluster +
  Redis Sentinel are the adopted deployment target; local development uses
  Docker Compose (plain Postgres 17 + plain Redis 7).** Add CNPG and the Redis
  operator to the §2 table (both "assumed pre-installed, cluster-level, like the
  Datadog Agent").
- **SPEC §3 infra list** — add: CNPG `Cluster`, CNPG `ScheduledBackup`, the
  `RedisFailover` CR, and their vendored CRD schemas (`Cluster_v1.json`,
  `ScheduledBackup_v1.json`, `Backup_v1.json`, `RedisFailover_v1.json`). Remove
  `backup-cronjob.yaml` and (see §E) `backup-serviceaccount.yaml` from the tree
  listing. Update the "Cloud infrastructure this layout assumes" block: the
  daily `pg_dump` CronJob line becomes "CNPG-native scheduled Barman backups to
  the same S3 bucket/CMK."
- **SPEC §5.1 (Datadog subsection) — the stale sentence (review finding 3).**
  §5.1 today states: *"Non-secret DD config (port, enabled flags) lives in the
  ConfigMap; `DATABASE_URL`/`REDIS_URL` stay in the SOPS Secret unchanged."*
  After this batch that is **factually wrong** and contradicts §9/§F:
  `DATABASE_URL` is no longer in SOPS / Secrets Manager at all (CNPG owns it via
  the generated `pg-app` Secret), and `REDIS_URL` is replaced by
  `REDIS_PASSWORD`. **Rewrite the clause** to: *"Non-secret DD config (port,
  enabled flags) lives in the ConfigMap; the Redis password lives in the SOPS
  Secret as `REDIS_PASSWORD` (materialized by ESO), and the Postgres credential
  is supplied by CNPG's generated `pg-app` Secret — not SOPS."* Keep the
  sentence's actual point (DD config is non-secret) intact. This is listed
  explicitly so the §5 edit (the config table, below) and the §5.1 edit are not
  conflated — they are two separate locations in §5.
- **SPEC §5 config table** — replace the single `REDIS_URL` row with the Sentinel
  shape (see §D): `REDIS_SENTINEL_HOSTS`, `REDIS_MASTER_NAME`, `REDIS_PASSWORD`
  for cluster; `REDIS_URL` kept as the local-dev fallback. For Postgres, show
  the two modes: `DATABASE_URL` (local-dev plain DSN) OR the discrete
  `PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DBNAME` set projected from CNPG's
  `pg-app` Secret in-cluster (composed into `database_dsn`); note the
  query-string-free asyncpg requirement (§D/§F).
- **SPEC §7** — migrations still run via the Argo CD PreSync migration Job, now
  explicitly **against the CNPG primary (`pg-rw` Service)**. Add the one-time
  first-boot ordering caveat (migration Job may retry until the CNPG primary is
  ready). **Update the "`migrations/env.py` reads `DATABASE_URL` from
  `app.config.Settings`" line** (SPEC §7, the first bullet): `env.py` now reads
  the composed `database_dsn` (which resolves to `DATABASE_URL` locally or the
  discrete `PG_*` set in-cluster), not `database_url` directly — matching the
  `migrations/env.py` `get_url()` change in §F / the Modify table.
- **SPEC §9** — secrets: the Redis password still flows
  SOPS→Secrets-Manager→ESO as a single stored property `REDIS_PASSWORD`, which
  ESO materializes into **two** `app-secrets` keys — `REDIS_PASSWORD` (app) and
  `password` (the Spotahome operator's required key). The DB credential is
  **no longer** a SOPS/Secrets-Manager key — the app reads discrete `PG_*` env
  projected from CNPG's generated `pg-app` Secret and composes `database_dsn`.
  Document the reconciled flow (§F).
- **SPEC §10** — CNPG `ScheduledBackup` (Barman→S3) replaces the `pg_dump`
  CronJob in the CI/CD narrative. `bootstrap_backups.py` still provisions the
  bucket + CMK.
- **SPEC §13** — mark the managed-DB/cache open question **Decided → ADR 0001**
  (there is no numbered open question for DB/cache today; add a short "Decided"
  line mirroring the Q9 observability entry, pointing at ADR 0001 and §2).

Local development on Docker Compose is explicitly stated **unchanged**
everywhere it is mentioned.

---

## D. Application code change for Redis Sentinel (RESOLVED)

### The config shape and exactly how the app switches modes

`src/app/config.py` gains three **optional** Sentinel fields alongside an
**optional** `redis_url`. Mode is chosen by a single rule:

> **If `redis_sentinel_hosts` is set (non-empty), use Sentinel mode; otherwise
> use `redis_url` (plain, local dev).**

```python
class Settings(BaseSettings):
    # Postgres — two mutually exclusive modes:
    # (1) local dev / plain: database_url (postgresql+asyncpg://…)
    # (2) cluster: discrete fields from CNPG's generated pg-app Secret
    database_url: str | None = None
    pg_host: str | None = None
    pg_port: int | None = None
    pg_user: str | None = None
    pg_password: str | None = None
    pg_dbname: str | None = None

    # Redis — two mutually exclusive modes:
    # (1) local dev / plain: redis_url (redis://[:pw]@host:port/db)
    # (2) cluster HA: Sentinel — sentinel_hosts + master_name (+ optional password)
    redis_url: str | None = None
    redis_sentinel_hosts: str | None = None  # "h1:26379,h2:26379,h3:26379"
    redis_master_name: str = Field(default="mymaster", min_length=1)
    redis_password: str | None = None  # Sentinel + master auth

    @model_validator(mode="after")
    def _require_one_mode_each(self) -> "Settings":
        if not self.redis_sentinel_hosts and not self.redis_url:
            raise ValueError("set REDIS_SENTINEL_HOSTS or REDIS_URL")
        discrete = (
            self.pg_host,
            self.pg_port,
            self.pg_user,
            self.pg_password,
            self.pg_dbname,
        )
        if not self.database_url and not all(discrete):
            raise ValueError("set DATABASE_URL or the full PG_* discrete set")
        return self

    @property
    def redis_is_sentinel(self) -> bool:
        return bool(self.redis_sentinel_hosts)

    @property
    def database_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        # all PG_* guaranteed set by the validator when database_url is None
        from urllib.parse import quote

        # Percent-quote BOTH user and password (review round-3 finding 4). CNPG's
        # generated app-role name is the DNS-label-safe `app`, so quoting the user
        # is a no-op today — but database_dsn presents itself as a general DSN
        # composer, so a role name with a URL-reserved char (`@`, `:`) must not
        # produce a malformed DSN. Quoting both keeps the composer correct
        # regardless of the role name.
        user = quote(self.pg_user or "", safe="")
        pw = quote(self.pg_password or "", safe="")
        return (
            f"postgresql+asyncpg://{user}:{pw}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_dbname}"
        )
```

- `database_url` becomes **optional** (cluster uses the discrete `PG_*` set
  projected from CNPG's `pg-app` Secret; see §F). Exactly one DB mode is
  required by the validator.
- `redis_url` becomes **optional** (so Sentinel-only prod does not need it).
- `REDIS_SENTINEL_HOSTS` is parsed from a comma-separated `host:port` string
  into `list[tuple[str, int]]` inside `cache.py` (not stored as a parsed field,
  to keep `Settings` a flat env-mapped model). A helper
  `parse_sentinel_hosts(raw: str) -> list[tuple[str, int]]` lives in `cache.py`,
  typed, validating each entry has a host and an int port (raises `ValueError`
  on malformed input — surfaced at startup, fatal, logged by the lifespan).
- `redis_master_name` defaults to `mymaster` (the operator default).
- The `model_validator` makes "neither set" a **fatal startup error** (clear
  message), so a misconfigured prod never silently starts with no Redis.
- **"Both set" precedence is intentional and silent — so `.env.example` must
  guard against it (review finding 4).** The validator does NOT treat "both
  `REDIS_URL` and `REDIS_SENTINEL_HOSTS` set" as an error; `redis_is_sentinel`
  is `bool(redis_sentinel_hosts)`, so **Sentinel wins** whenever the hosts are
  present. This is deliberate (in-cluster never sets `REDIS_URL`, so the only way
  to reach the "both set" state is a local developer uncommenting the Sentinel
  example while leaving `REDIS_URL` in place — which would then silently try to
  reach a non-existent Sentinel and fail against the Compose Redis). To keep the
  "local dev unchanged" promise honest, **`.env.example` must present the
  cluster-only vars commented out AND explicitly mutually exclusive with
  `REDIS_URL`.** The concrete block (do not paraphrase):

  ```dotenv
  DATABASE_URL=postgresql+asyncpg://app:local-only-password@localhost:5432/app
  REDIS_URL=redis://:local-only-password@localhost:6379/0

  # --- Cluster-only Redis Sentinel mode (DO NOT set locally) ---
  # In Kubernetes the app talks to Redis via Sentinel, not a single URL. These
  # are set by the ConfigMap in-cluster; they are shown here only for reference.
  # Setting REDIS_SENTINEL_HOSTS switches the app to Sentinel mode and makes it
  # IGNORE REDIS_URL. If you uncomment these for a local Sentinel experiment,
  # you MUST remove/comment REDIS_URL above — otherwise the app silently picks
  # Sentinel and fails to reach the plain Compose Redis.
  # REDIS_SENTINEL_HOSTS=rfs-redis:26379
  # REDIS_MASTER_NAME=mymaster
  # REDIS_PASSWORD=local-only-password
  #
  # In-cluster DATABASE_URL is NOT set directly: it is composed from the
  # discrete PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DBNAME env the Deployment
  # projects from CNPG's generated pg-app Secret (see docs/configuration.md).
  ```

  The leading two lines are the existing local-dev defaults (unchanged); the
  commented block documents the cluster vars and the mutual-exclusion rule so a
  developer cannot stumble into silent Sentinel mode.

A single mypy-strict note: the validator returns `Settings`; annotate as a
forward ref string per the repo's existing style. No `Any`. `Field` is imported
from `pydantic` (the `min_length=1` on `redis_master_name` enforces the
"non-empty" rule the validation table states — review finding 6); the
`pydantic-settings` plugin already understands `Field` so `mypy --strict` stays
clean.

### Client construction change (behavior preserved, only construction changes)

`src/app/cache.py`:

```python
from redis.asyncio import Redis
from redis.asyncio.sentinel import Sentinel


def create_redis(settings: Settings) -> Redis:
    """Build the shared async Redis client (Sentinel master, or plain URL)."""
    if settings.redis_sentinel_hosts:
        hosts = parse_sentinel_hosts(settings.redis_sentinel_hosts)
        sentinel = Sentinel(
            hosts,
            sentinel_kwargs={"password": settings.redis_password},
            password=settings.redis_password,
            decode_responses=True,
        )
        return sentinel.master_for(settings.redis_master_name)
    assert settings.redis_url is not None  # guaranteed by Settings validator
    return Redis.from_url(settings.redis_url, decode_responses=True)
```

- Signature changes from `create_redis(redis_url: str)` to
  `create_redis(settings: Settings)` so the factory can see all Redis fields.
  `main.py`'s lifespan changes its one call site to `create_redis(resolved)`.
- `master_for(...)` returns a `Redis` client that transparently talks to the
  **current master** (Sentinel-discovered); `get`/`set`/`delete`/`ping` are
  identical, so **`health.py` and `items.py` need NO changes** — cache-aside and
  the `PING` readiness check are byte-for-byte the same behavior. Only the
  construction call changes.
- `sentinel_kwargs={"password": ...}` authenticates to the sentinels; the
  top-level `password=` authenticates to the discovered master. Both come from
  the single `REDIS_PASSWORD`.
- `aclose()` on shutdown is unchanged (the `master_for` client supports it).
- **`decode_responses=True` propagation (review finding 9).** `decode_responses`
  is passed to the `Sentinel(...)` constructor and reaches the `master_for(...)`
  client via connection kwargs (redis-py copies the Sentinel's connection kwargs
  into the master pool). `health.py` and `items.py` assume decoded `str`
  responses (the plain path sets `decode_responses=True` on `from_url`). So the
  two paths are behaviorally identical. The `test_cache.py` unit test **asserts
  this explicitly**: the mocked `Sentinel(...)` call is checked to receive
  `decode_responses=True`, pinning the propagation rather than trusting it
  silently.
- The `assert settings.redis_url is not None` carries a comment; it is not an
  `S101` concern (not a test) and documents the invariant the validator owns.
  If ruff flags `B011`/`S101` in app code, replace with an explicit
  `if … raise RuntimeError(...)` — decided at implementation after running ruff.

### Validation rules for the new inputs

| Input                                                   | Required                                | Type                      | Rule / failure behavior                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------- | --------------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `REDIS_SENTINEL_HOSTS`                                  | optional (required in cluster)          | `str` → `list[(str,int)]` | comma-separated `host:port`; each port an int in 1–65535; malformed → `ValueError` at startup (fatal, logged)                                                                                                                                                                                                  |
| `REDIS_MASTER_NAME`                                     | optional                                | `str`                     | defaults `mymaster`; **enforced non-empty** via `Field(min_length=1)` (review finding 6) — an explicit `REDIS_MASTER_NAME=""` is rejected at startup, not deferred to an opaque `master_for("")` failure                                                                                                       |
| `REDIS_PASSWORD`                                        | optional                                | `str`                     | used for both sentinel and master auth when present                                                                                                                                                                                                                                                            |
| `REDIS_URL`                                             | optional (required local)               | `str`                     | plain URL; used only when `REDIS_SENTINEL_HOSTS` unset                                                                                                                                                                                                                                                         |
| `DATABASE_URL`                                          | optional (required local)               | `str`                     | plain DSN; used only when the `PG_*` set is absent; `db.py` rejects **only** the known-bad libpq query params (`sslmode`/`sslrootcert`/`sslcert`/`sslkey`/`sslpassword`/`gssencmode`/`sslnegotiation`), naming the offender — other query params (incl. a bare `ssl=`) pass through (review round-3 finding 1) |
| `PG_HOST`/`PG_PORT`/`PG_USER`/`PG_PASSWORD`/`PG_DBNAME` | optional as a set (required in cluster) | `str`/`int`               | projected from CNPG `pg-app` Secret; all five required together to compose `database_dsn`                                                                                                                                                                                                                      |
| (Redis invariant)                                       | —                                       | —                         | at least one of `REDIS_SENTINEL_HOSTS` / `REDIS_URL` must be set; else fatal startup error                                                                                                                                                                                                                     |
| (DB invariant)                                          | —                                       | —                         | `DATABASE_URL` **or** the full `PG_*` set must be present; else fatal startup error                                                                                                                                                                                                                            |

Invariant ownership: **`Settings` (the config layer) owns "exactly one Redis
mode and one DB mode is configured"** because it is the single place all env
inputs converge and the earliest point a misconfiguration can be rejected (fail
fast at process start, before the lifespan opens connections). `database_dsn`
additionally owns the guarantee that the *composed* string handed to asyncpg
carries no query string at all (it is built field-by-field and never adds one).
The separate concern — a hand-pasted local `.env` DSN carrying a libpq-only
query param asyncpg cannot consume — is owned by **`db.py`'s
parameter-specific guard** (§F): it rejects only the known-bad libpq keys
(`sslmode` et al.) and names the offender, leaving every other query param
(including a bare `ssl=`) untouched, so a valid local DSN is never a false
positive (review round-3 finding 1).

### Tests (unit run with NO services; integration stays marked)

- `tests/unit/test_config.py` — `redis_url` and `database_url` both become
  individually non-required (each has an alternative). Changes:
  - **`test_missing_required_field_raises` (review finding 4):** it is currently
    parametrized `["DATABASE_URL", "REDIS_URL"]` and asserts each is
    individually required. After the change **neither is individually
    required** (`DATABASE_URL` has the discrete `PG_*` alternative; `REDIS_URL`
    has the Sentinel alternative), so the old parametrization encodes a false
    contract — with one removed the *other mode's* validator fires and the test
    passes for the wrong reason. **Replace it** with explicit mode-matrix cases
    rather than reducing it to a single field:
      - DB: `DATABASE_URL` set, no `PG_*` → ok; full `PG_*` set, no
        `DATABASE_URL` → ok (`database_dsn` composes); neither → `ValidationError`.
      - Redis: `REDIS_SENTINEL_HOSTS` set, no `REDIS_URL` → ok,
        `redis_is_sentinel is True`; `REDIS_URL` set → ok,
        `redis_is_sentinel is False`; neither → `ValidationError`.
  - Add a `database_dsn` composition test (discrete `PG_*` → clean
    `postgresql+asyncpg://…` with no query string; **both username and password
    percent-quoted** — review round-3 finding 4; include a case with a
    URL-reserved char in the user/password to prove the quoting).
  - Add a case asserting `REDIS_MASTER_NAME=""` raises `ValidationError`
    (the `Field(min_length=1)` rule, review finding 6), so the table claim and
    the code agree.
  - Update `test_required_fields_read_from_env` so it no longer asserts either
    `DATABASE_URL` or `REDIS_URL` is *required* — it may still assert they are
    *read* when present.
- **New** `tests/unit/test_cache.py` — unit tests for `parse_sentinel_hosts`
  (valid, malformed, bad port) and for `create_redis` **mode selection** using
  `monkeypatch`/mocks on `redis.asyncio.sentinel.Sentinel` and `Redis.from_url`
  so **no network/service** is touched: assert Sentinel path constructs a
  `Sentinel(...)` with the parsed hosts and calls `.master_for(master_name)`;
  assert plain path calls `Redis.from_url(redis_url, ...)`. Pure unit.
- `tests/conftest.py` `placeholder_env` — keep setting `DATABASE_URL` +
  `REDIS_URL` (plain mode) so existing app-building tests stay in local mode.
- `tests/integration/test_items_api.py` — unchanged; still runs against plain
  local Redis via `REDIS_URL` (Docker Compose). Integration tests do NOT spin up
  Sentinel — local dev is plain Redis by design; the Sentinel construction is
  covered by the mocked unit test above. Documented in the test's docstring.
- All new code: `mypy --strict` clean (annotate the validator return, the
  parser return type, the factory signature), ruff clean, Pydantic models remain
  the only request/response types (no new request/response models added).

---

## E. Backup reconciliation (RESOLVED)

### Removed

- **`deploy/k8s/base/backup-cronjob.yaml`** — deleted. The `pg_dump`-to-S3
  CronJob is superseded by CNPG-native Barman backups.
- **`deploy/k8s/base/backup-serviceaccount.yaml`** — deleted. CNPG uses **its
  own** ServiceAccount for the cluster pods; Barman→S3 auth is configured on the
  `Cluster`/`ScheduledBackup` via `spec.backup.barmanObjectStore`, and the IRSA
  role is annotated on the **CNPG cluster's ServiceAccount** through
  `spec.serviceAccountTemplate.metadata.annotations["eks.amazonaws.com/role-arn"]
  = PLACEHOLDER_BACKUP_ROLE_ARN`. The old standalone `backup` SA is obsolete.
- Both removed from `deploy/k8s/base/kustomization.yaml` `resources:`.
- The per-overlay `db-backup` `BACKUP_PREFIX` **patches** (dev + prod
  kustomizations) — removed (the CronJob they patched is gone). The Barman S3
  path is instead per-env via the `ScheduledBackup`/`Cluster` `barmanObjectStore`
  `destinationPath` / `serverName` set per overlay (below).

### Added

- **CNPG `Cluster` `spec.backup.barmanObjectStore`** pointing at the SAME bucket
  and CMK `bootstrap_backups.py` provisions:
  ```yaml
  backup:
    barmanObjectStore:
      destinationPath: s3://PLACEHOLDER_BACKUP_BUCKET/PLACEHOLDER_BACKUP_PREFIX
      s3Credentials:
        inheritFromIAMRole: true        # use the IRSA role on CNPG's SA
      wal: { compression: gzip }
      data: { compression: gzip }
      # SSE-KMS with the backup CMK:
      # (CNPG passes encryption via the object store; the bucket's default
      #  SSE-KMS from bootstrap_backups.py already encrypts at rest with the CMK)
    retentionPolicy: "30d"
  ```
- **`deploy/k8s/base/scheduledbackup.yaml`** — `apiVersion postgresql.cnpg.io/v1`,
  `kind: ScheduledBackup`, `spec.schedule: "0 0 3 * * *"`,
  `spec.cluster.name: pg`, `spec.backupOwnerReference: self`. Added to base
  `kustomization.yaml`. **Field-order note (review finding 10):** CNPG's
  `spec.schedule` is a **6-field** (robfig/cron, seconds-leading) expression, so
  `"0 0 3 * * *"` = 03:00:00 UTC daily — it is NOT the standard 5-field cron the
  old `backup-cronjob.yaml` and other repo CronJobs use. The manifest MUST carry
  an inline comment stating "CNPG 6-field (seconds-first) cron, intentional —
  not 5-field" so an implementer does not "normalize" it to 5 fields and
  silently shift the schedule.
- **Per-env Barman path.** `destinationPath` carries `PLACEHOLDER_BACKUP_PREFIX`
  in base; each overlay patches the `Cluster`'s
  `spec.backup.barmanObjectStore.destinationPath` (or sets `serverName` per env)
  to `dev`/`prod` so dev and prod WAL/base backups never collide under one
  bucket — the direct analogue of the old `BACKUP_PREFIX` patch. The overlays'
  CronJob patch is replaced by this Cluster patch.

### Bucket/CMK reuse (no second provisioning)

`scripts/bootstrap_backups.py` is **kept**: same S3 bucket + backup-only CMK +
`--dry-run` default + `--apply` placeholder guard + `--retention-days 30`
default + unit tests. Barman writes under a per-env prefix *inside* that same
bucket, so the bucket-level default SSE-KMS (set by the script) encrypts every
object with the CMK — no per-object `--sse-kms-key-id` needed. **Adjustment to
the script:** the lifecycle rule currently uses `Filter.Prefix: ""` (whole
bucket); it stays correct for Barman's layout (objects live under
`<prefix>/base/…`, `<prefix>/wals/…`), so **no code change is required** beyond a
docstring note that the layout is now Barman's, not `db/<ts>.sql.gz`. The
`--retention-days` lifecycle rule (S3-side) complements CNPG's
`retentionPolicy: "30d"` (Barman-side); both documented as the same 30-day
window. Its `test_bootstrap_backups.py` tests are unaffected (they assert plan
text + the placeholder guard, not the backup mechanism).

### NetworkPolicy reconciliation

`deploy/k8s/base/networkpolicy.yaml` egress rule 2 (the any-IP port list) is
reworked:

- **Keep** 5432/TCP — the app + migration Job now reach the **in-cluster
  `pg-rw` CNPG Service** on 5432 (same port, now in-cluster).
- **Keep** 6379/TCP (redis master/replicas) and **add 26379/TCP** (Sentinel
  port) — the app's Sentinel client connects to sentinels on 26379 and to the
  discovered master on 6379.
- **Keep** 8126/TCP + 8125/UDP (Datadog), unchanged.
- **Remove the backup-only 443/TCP rule's justification tied to the deleted
  CronJob.** The app pods themselves do not call S3/STS/KMS. **However, 443 is
  NOT simply deleted blindly:** verify whether any remaining selected pod needs
  it. The app and migration pods do not. CNPG's own pods (which DO call S3 for
  Barman) are **not** selected by this policy (they carry CNPG's labels, not
  `app.kubernetes.io/name: <slug>`) and are governed by whatever policy the CNPG
  namespace/operator applies — so **443 is removed** from this app policy, and a
  comment notes CNPG's backup egress is the operator's concern, not this policy.
  The DNS rule (rule 1) is unchanged.
- The header comment is rewritten: egress now targets in-cluster CNPG + Redis
  Sentinel Services (5432/6379/26379) + DNS + Datadog; the top-of-file "backup
  pod needs 443" block is removed. **Both backup references must go (review
  finding 8), not just the header:** (1) the top-of-file egress description that
  lists 443 for "S3, STS, KMS, Secrets Manager"; (2) the inline comment on the
  443 port entry ("The backup CronJob pod … needs 443 for S3, STS … KMS …"); and
  (3) the `TIGHTEN`-example clause in the commented block — "any future
  tightening MUST preserve the backup CronJob pod's egress to Postgres (5432)
  and to 443 (S3, STS, KMS, Secrets Manager) …". The CronJob pod no longer
  exists, so every one of those clauses is dead and misleading. The "selected by
  port only / tighten with a `to:` selector" guidance stays, and the commented
  tighten-example is rewritten to reference the CNPG (`pg-rw`/`pg-ro`/`pg-r`) and
  Redis (`rfs-redis`/`rfr-redis`) Service labels instead of a generic
  `postgres` podSelector.
- kube-linter stays clean (NetworkPolicy is not deep-linted for port content);
  verified on the render.

### `.kube-linter.yaml` stale-exclusion reconciliation (review finding 2 — MEDIUM)

`deploy/k8s/.kube-linter.yaml` currently excludes **two** checks, and both
written reasons (SPEC §14.9) point at `backup-cronjob.yaml`, which this batch
**deletes**. Leaving them stale is itself a §14.9 violation (an exclusion with a
false reason). This file is therefore in the Modify table with a concrete,
**run-the-linter-first** plan (do not pre-decide the outcome):

- **`latest-tag`** — reason today: the backup CronJob's image is the untagged
  `PLACEHOLDER_BACKUP_IMAGE`. After deletion the only images left are
  `deployment.yaml` / `migration-job.yaml`, both tagged `:0.0.0`, so the untagged
  reference is gone. The new CNPG `Cluster` / `RedisFailover` / `ScheduledBackup`
  CRs are **unknown kinds** kube-linter does not deep-lint (it targets workload
  kinds), so they should not reintroduce a `latest-tag` finding — but
  **confirm by re-running kube-linter on the CronJob-deleted dev/prod renders.**
  If `latest-tag` no longer fires, **remove the exclusion** (dead). If it still
  fires for any reason, rewrite its reason to the real cause.
- **`job-ttl-seconds-after-finished`** — reason today points at the backup
  CronJob's `jobTemplate.ttlSecondsAfterFinished`. The **migration Job**
  (`migration-job.yaml`) *also* sets `ttlSecondsAfterFinished: 3600` (verified),
  so this check very likely **still fires** after the CronJob is gone — meaning
  the exclusion is probably still needed, but its stated reason is now wrong
  (names a deleted file). **Rewrite the reason to reference the migration Job**
  (the PreSync hook sets `ttlSecondsAfterFinished` to reap finished migrate Jobs
  promptly). If, on the render, it turns out not to fire, remove it instead.

This is the same stale-reference class the design already fixed for the
NetworkPolicy comments and the `placeholders.yml` NOTE — the kube-linter config
was the third place the deleted CronJob is referenced. Ships in **commit 3**
(the backup-removal commit) alongside the CronJob deletion.

### PLACEHOLDER_BACKUP_* delta

- `PLACEHOLDER_BACKUP_IMAGE` — **removed** (no `pg_dump` container image any
  more; CNPG uses the operator's Postgres image). Delete its `fill` entry.
- `PLACEHOLDER_BACKUP_BUCKET` — **kept** (now a Barman `destinationPath`
  component instead of an `aws s3 cp` target); update its `files:` list from
  `backup-cronjob.yaml` to the CNPG `Cluster` manifest + `scripts/bootstrap_backups.py`.
- `PLACEHOLDER_BACKUP_KMS_KEY_ID` — **kept** (bucket default SSE-KMS); update
  `files:` from `backup-cronjob.yaml` to the CNPG `Cluster` manifest +
  `scripts/bootstrap_backups.py`. (CNPG inherits the bucket's default encryption;
  the key id token now documents the bucket's CMK rather than an explicit
  `--sse-kms-key-id` CLI arg.)
- `PLACEHOLDER_BACKUP_KMS_ALIAS` — **kept** (bootstrap script), unchanged.
- `PLACEHOLDER_BACKUP_ROLE_ARN` — **kept** but **moved**: from the deleted
  `backup-serviceaccount.yaml` to the CNPG `Cluster`'s
  `spec.serviceAccountTemplate` IRSA annotation. Update `files:`.

---

## F. Secrets / ESO credential reconciliation (RESOLVED)

### Postgres `DATABASE_URL`: sourced from the CNPG-generated Secret

**Decision: CNPG is the single source of truth for the Postgres app
credential.** CNPG generates a Secret named `pg-app` (cluster name `pg` +
`-app`) containing the app role's `username`, `password`, and discrete
connection fields (`host`, `port`, `dbname`) plus a ready-made `uri`. The app
consumes the **discrete** fields (not `uri` — see the asyncpg caveat below) and
composes the DSN. `DATABASE_URL` is **no longer** a key in the SOPS file /
Secrets Manager / the ESO `ExternalSecret`.

**CNPG-generated Secret name and keys (review finding 5 — resolved explicitly).**
The `Cluster` **does NOT set `spec.bootstrap.initdb.secret`**. Omitting it is
what makes CNPG *generate* (rather than consume) the application credential: for
a cluster named `pg` with `bootstrap.initdb.database: app` / `owner: app`, CNPG
creates a Secret named **`pg-app`** (`<cluster>-app`) of type
`kubernetes.io/basic-auth` with keys `username`, `password`, `host`, `port`,
`dbname`, `uri`, `jdbc-uri`, `pgpass`. (If a user-supplied `initdb.secret` were
named, CNPG would consume it and NOT generate `pg-app` — so the manifest MUST
omit it. Confirm the exact generated key set against the shipped CNPG 1.24+ line
at implementation; `username`/`password`/`host`/`port`/`dbname` are stable
across the 1.x line and are the composition inputs below, so the design does not
hinge on the `uri` key existing.)

How the app receives it: the Deployment and migration Job build `DATABASE_URL`
**from the CNPG `pg-app` Secret** rather than from `app-secrets`.

**Chosen mechanism (revised per review finding 2): compose from the discrete
CNPG keys, NOT the ready-made `uri`.** The earlier design fed CNPG's `uri` key
straight into asyncpg after only a scheme rewrite. That is **unsafe and was
verified to fail**: CNPG's `uri` is a libpq-style URL that can carry query
params such as `sslmode=`, and asyncpg does not understand `sslmode` —
SQLAlchemy's asyncpg dialect raises `TypeError: connect() got an unexpected
keyword argument 'sslmode'` at connect time (reproduced locally against
`postgresql+asyncpg://…?sslmode=require`). A scheme-only rewrite leaves the
query string intact, so the pod would fail to connect in-cluster. Two safe
options were considered:

- (rejected) Keep `secretKeyRef → uri` and have `db.py` strip/translate libpq
  query params (drop `sslmode`, translate to asyncpg `ssl=`). Workable but
  fragile: it couples the app to the exact libpq param set CNPG emits per
  version, and the stripping logic is easy to get subtly wrong.
- **(chosen) Build `DATABASE_URL` from the discrete `pg-app` keys**
  (`username`, `password`, `host`, `port`, `dbname`) so no libpq query string
  ever reaches asyncpg, then normalize the scheme in `db.py`.

Concretely:

  - **Deployment + migration Job env.** Project the discrete CNPG keys as env
    vars and let `db.py` assemble the URL — but to keep assembly in typed Python
    (Kubernetes manifests cannot concatenate), project them under **well-known
    names** the app reads:
    ```yaml
    env:
      - name: PG_HOST
        valueFrom: { secretKeyRef: { name: pg-app, key: host } }
      - name: PG_PORT
        valueFrom: { secretKeyRef: { name: pg-app, key: port } }
      - name: PG_USER
        valueFrom: { secretKeyRef: { name: pg-app, key: username } }
      - name: PG_PASSWORD
        valueFrom: { secretKeyRef: { name: pg-app, key: password } }
      - name: PG_DBNAME
        valueFrom: { secretKeyRef: { name: pg-app, key: dbname } }
    ```
    `app-secrets` `envFrom` stays (it carries `REDIS_PASSWORD`); `DATABASE_URL`
    is no longer one of its keys.
  - **`config.py`.** `database_url` becomes **optional**, and five optional
    discrete fields are added: `pg_host`, `pg_port: int | None`, `pg_user`,
    `pg_password`, `pg_dbname`. A `@property database_dsn: str` returns
    `database_url` when set (local dev / `.env`), else composes
    `postgresql+asyncpg://{user}:{quote(password)}@{host}:{port}/{dbname}` from
    the discrete fields (password percent-quoted via `urllib.parse.quote`). A
    `model_validator` requires **either** `database_url` **or** the full
    discrete set — "neither" is a fatal startup error (mirrors the Redis
    two-mode validator). The invariant "exactly one DB config mode" is owned by
    `Settings`, same layer and reason as Redis.
  - **`db.py` — parameter-specific libpq guard, NOT a blanket `?` ban
    (review round-3 finding 1).** `create_engine(database_url: str)` still
    normalizes a leading `postgresql://` → `postgresql+asyncpg://` (defensive,
    for the local-dev `.env` case where someone pastes a plain libpq URL). It
    then **rejects only the specific libpq query parameters the SQLAlchemy
    asyncpg dialect cannot consume**, rather than any URL containing a `?`. A
    blanket `?` ban is a behavior regression: the asyncpg dialect legitimately
    accepts several query params that are NOT libpq (e.g. the SQLAlchemy-level
    `prepared_statement_cache_size`, and the dialect's own `ssl=`/`sslmode=`
    *when it is the dialect form* — but critically the libpq `sslmode=`,
    `sslrootcert=`, `sslcert=`, `sslkey=`, `sslpassword=` keys are the ones that
    blow up as `connect() got an unexpected keyword argument`). A developer with
    a working local `DATABASE_URL=postgresql+asyncpg://.../d?ssl=disable` must
    not be newly rejected — that would break the "local dev unchanged" promise.

    Concrete guard behavior:
    - Parse the URL's query with `urllib.parse.urlsplit` + `parse_qsl`.
    - Reject the URL (raise `ValueError`) **only** if the query contains a key in
      the known-bad libpq set
      `{"sslmode", "sslrootcert", "sslcert", "sslkey", "sslpassword", "gssencmode", "sslnegotiation"}`
      (case-insensitively). These are the libpq-only keys asyncpg/SQLAlchemy's
      asyncpg dialect does not translate and that surface as a cryptic
      `TypeError` at connect time.
    - The `ValueError` message **names the offending parameter(s)** and points
      the reader at the asyncpg `ssl=`/`connect_args` form, so the failure is
      actionable (e.g. `"DATABASE_URL contains libpq-only query parameter
      'sslmode' which asyncpg cannot consume; use the asyncpg 'ssl=' form or
      pass TLS options via connect_args"`).
    - **Any other query parameter is left untouched** (passed through to the
      dialect), so valid asyncpg/SQLAlchemy query params and a bare `?ssl=...`
      keep working.

    The composed `database_dsn` from `config.py` never carries a query string at
    all (it is built field-by-field), so the in-cluster path is always clean and
    never trips the guard; the guard exists purely to catch a stray libpq param
    in a hand-pasted local `.env` DSN with a readable message. `main.py`'s
    lifespan passes `resolved.database_dsn` (not `resolved.database_url`) to
    `create_engine`.
  - **Tests (`tests/unit/test_db.py`, new).** Unit tests, no DB connection:
    (a) scheme normalization `postgresql://` → `postgresql+asyncpg://`;
    (b) an already-`+asyncpg` URL is untouched;
    (c) a URL with a libpq `?sslmode=require` query raises `ValueError` **and the
    message names `sslmode`** (the parameter-specific guard — review round-3
    finding 1); and
    (d) **a URL carrying a NON-libpq query param — e.g.
    `postgresql+asyncpg://app:pw@h:5432/app?prepared_statement_cache_size=0`, and
    the bare `?ssl=disable` form — is ACCEPTED unchanged** (the guard must not
    blanket-ban `?`, so this case pins the "valid local DSN still works"
    contract);
    and in `test_config.py`, that `database_dsn` composes the expected
    query-string-free URL from discrete fields and that percent-quoting handles a
    username and a password with special characters.

This keeps a single generator (CNPG) as the source of truth for the DB
credential while guaranteeing the string handed to asyncpg is always a clean,
query-param-free `postgresql+asyncpg://` URL.

  - **`migrations/env.py` (review finding 1 — HIGH).** This module's
    `get_url() -> str` currently returns `Settings().database_url`. Because
    `database_url` becomes `str | None` and the in-cluster migration Job is
    given only the discrete `PG_*` env (no `DATABASE_URL`), the current form
    **both** breaks at runtime (the PreSync hook's `create_async_engine(None, …)`
    fails on every cluster deploy) **and** fails `mypy --strict` (`-> str`
    returning `str | None`). The fix is a one-line change that mirrors
    `main.py`'s lifespan — read the composed DSN:
    ```python
    def get_url() -> str:
        return (
            Settings().database_dsn
        )  # composed from PG_* in-cluster, or DATABASE_URL locally
    ```
    The `-> str` annotation stays correct (`database_dsn` is `-> str`). Both the
    offline (`context.configure(url=get_url(), …)`) and async-online
    (`create_async_engine(get_url(), …)`) paths then get a clean,
    query-string-free DSN. This edit ships in **commit 1** (the config-contract
    commit) so the template stays green — it is part of the same
    `Settings` contract change, and without it commit 1's "verified by
    `uv run poe check`" claim is false.

Rationale: a single generator (CNPG) owns the DB credential, so rotation,
failover endpoint, and password are always consistent — there is no second copy
in Secrets Manager to drift. (Alternative — keep `DATABASE_URL` in
SOPS→Secrets-Manager→ESO pointing at the CNPG Service DNS with a
human-maintained password — rejected: it duplicates the credential CNPG already
owns and reintroduces the manual-rotation problem the whole chain was built to
remove.)

### Redis password: stays SOPS → Secrets Manager → ESO, one key for both app and operator

**Decision:** the Redis Sentinel password remains a SOPS-managed secret. The ESO
`ExternalSecret` key set for `app-secrets` changes:

- **before:** `{ DATABASE_URL, REDIS_URL }`
- **after:** `{ REDIS_PASSWORD }` (DB removed → CNPG; Redis reduced to the
  password, since host discovery is via Sentinel Services, not a URL).

The `RedisFailover` CR's `spec.auth.secretPath: app-secrets` reads **the same
`app-secrets` Secret**'s **`password`** key (the Spotahome operator convention —
review finding 1; NOT `REDIS_PASSWORD`). The app reads **`REDIS_PASSWORD`** from
the same Secret. To keep both consuming one value with no drift, the ESO
`ExternalSecret` materializes **both keys from the single Secrets Manager
value** `REDIS_PASSWORD`:

```yaml
# deploy/k8s/overlays/*/externalsecret.yaml (template form, explicit keys)
spec:
  target:
    name: app-secrets
  data:
    - secretKey: REDIS_PASSWORD           # consumed by the app (pydantic-settings)
      remoteRef:
        key: <slug>/<env>/app-secrets
        property: REDIS_PASSWORD
    - secretKey: password                 # consumed by the Spotahome operator
      remoteRef:
        key: <slug>/<env>/app-secrets
        property: REDIS_PASSWORD          # SAME source value → identical string
```

This replaces the previous single `dataFrom.extract` (which produced one key per
Secrets Manager property) with an explicit two-entry `data:` block that maps the
one stored `REDIS_PASSWORD` property to both Secret keys. One stored value, two
Secret keys, guaranteed equal — the operator and the app authenticate with the
same password. `REDIS_SENTINEL_HOSTS` and `REDIS_MASTER_NAME` are **non-secret**
(Service DNS names + a group name) and live in the **ConfigMap** (`app-config`),
set for every cluster overlay to the operator's `rfs-redis` Service (see
finding 6 below).

### Downstream artifact changes (consistency)

- `sync-secrets.yml` validates the key set of each `secrets.<env>.yaml`. It
  currently asserts exactly `{DATABASE_URL, REDIS_URL}`; it must assert exactly
  `{REDIS_PASSWORD}`. **The string `DATABASE_URL`/`REDIS_URL` is hardcoded in
  SEVEN places (review finding 3); every one must change, or the job is left in
  a contradictory, never-passing state. Enumerated edits:**
  1. header comment "keys DATABASE_URL + REDIS_URL" → "key REDIS_PASSWORD".
  2. the IAM-note / secret-id commentary — no key names there, leave as is
     (the ARN scope is unchanged).
  3. `expected='["DATABASE_URL","REDIS_URL"]'` → `expected='["REDIS_PASSWORD"]'`.
  4. the extra-key filter
     `select(. != "DATABASE_URL" and . != "REDIS_URL")` →
     `select(. != "REDIS_PASSWORD")`.
  5. the missing-key loop `for k in DATABASE_URL REDIS_URL` → `for k in REDIS_PASSWORD`.
  6. the error message `(only DATABASE_URL and REDIS_URL are allowed)` →
     `(only REDIS_PASSWORD is allowed)` and
     `key set must be exactly {DATABASE_URL, REDIS_URL}` →
     `key set must be exactly {REDIS_PASSWORD}`.
  7. the success echo `key shape OK (DATABASE_URL, REDIS_URL)` →
     `key shape OK (REDIS_PASSWORD)`.
  **Decision on the per-key loop:** with a single required key the
  `keys | sort == '["REDIS_PASSWORD"]'` comparison already fully validates the
  shape (missing → the sorted set is `[]` or wrong; extra → the sorted set
  differs), so the `for k in …` loop is redundant. **Keep it** anyway (reduced
  to `for k in REDIS_PASSWORD`) so the "missing required key" message stays
  specific and the diff is minimal/obvious; it is a no-op on the happy path.
  The Secrets Manager entry `<slug>/<env>/app-secrets` now holds only
  `REDIS_PASSWORD`. **Scope note (review finding 5):** the seven sites above are
  the **key-set strings only** (`DATABASE_URL`/`REDIS_URL`). The
  `k8s-fastapi-starter` slug that also appears in the file's `SECRET_ID` and
  header is a separate `PROJECT_SLUG` concern (already covered by
  `PROJECT_SLUG.replace_in` via the `.github/workflows/sync-secrets.yml` entry)
  and is **left alone here** — an implementer must not "fix" the slug strings as
  if they were key-set strings.
- `deploy/k8s/overlays/*/externalsecret.yaml` — **changed from `dataFrom.extract`
  to an explicit two-key `data:` block** (see §F Redis section above): the one
  stored property `REDIS_PASSWORD` maps to **two** Secret keys —
  `REDIS_PASSWORD` (app) and `password` (Spotahome operator). This is a
  structural change, not just a renamed key.
- `deploy/k8s/overlays/*/secrets.example.yaml` — `stringData` becomes
  `REDIS_PASSWORD: PLACEHOLDER_REDIS_PASSWORD` only (drop `DATABASE_URL` /
  `PLACEHOLDER_DB_PASSWORD` rows — DB is CNPG-owned now). `PLACEHOLDER_DB_PASSWORD`
  is **removed** from `placeholders.yml` (no longer appears anywhere: CNPG
  generates the DB password; the example secret no longer carries it).
- `deploy/k8s/base/configmap.yaml` — add `REDIS_SENTINEL_HOSTS` and
  `REDIS_MASTER_NAME` (non-secret), base values pointing at the in-namespace
  `rfs-redis` Service (`rfs-redis:26379`) and `mymaster`.
  **Mode-selection clarity (review finding 6):** putting these in **base** means
  **every cluster overlay (both dev and prod) is Sentinel-mode** — which is
  correct, because both overlays deploy a `RedisFailover` and therefore run
  in-cluster Sentinel. This is intended; dev is NOT plain Redis in-cluster
  (local Docker Compose is the only plain-Redis environment). The base
  ConfigMap and the overlays MUST **not** carry `REDIS_URL` at all (it is
  neither in the ConfigMap nor in `app-secrets` now), so no pod ever has both
  `REDIS_SENTINEL_HOSTS` and `REDIS_URL` set — the `create_redis`
  "sentinel-hosts-wins" rule is therefore unambiguous in-cluster, and there is
  no silent-override hazard. **Local Docker Compose is unaffected**: it reads
  `.env` (`REDIS_URL`), never the cluster ConfigMap, so it stays on the plain
  path. The base default host (`rfs-redis:26379`) is correct in both namespaces
  because the operator names the Service the same per namespace; overlays patch
  only if an environment renames the `RedisFailover`.
- No plaintext secrets committed; `scripts/check_sops_encrypted.py` and gitleaks
  stay green (the example file holds only `PLACEHOLDER_REDIS_PASSWORD`).

---

## 4. CRD schemas + the k8s-check two-pass gate (RESOLVED)

### New vendored schema filenames (canonical kind casing — kubeconform 0.8.0 has no `toLower`)

Added under `deploy/k8s/crd-schemas/`:

- `Cluster_v1.json` — CNPG `Cluster`, `postgresql.cnpg.io/v1`
- `ScheduledBackup_v1.json` — CNPG `ScheduledBackup`, `postgresql.cnpg.io/v1`
- `Backup_v1.json` — CNPG `Backup`, `postgresql.cnpg.io/v1` (vendored for
  completeness / ad-hoc restores; not shipped as a manifest but keeps the schema
  dir self-describing)
- `RedisFailover_v1.json` — Spotahome `RedisFailover`, `databases.spotahome.com/v1`

#### Concrete schema sources + a CRD-derived fallback (review round-3 finding 2)

Vendoring these four JSON schemas is a **hard gate on commit 2**: the two-pass
`k8s-check` (pass (b), no `-ignore-missing-schemas`) **fails the build** unless
each file exists and the rendered CR validates against it. So the source is
named concretely for every kind, with a deterministic fallback so an implementer
is **never blocked** by an absent catalog entry. The schema dir is hermetic
(committed JSON), so these fetches happen **once**, at authoring time, not in CI.

**Primary source — the `datreeio/CRDs-catalog` repository** (the same catalog
batch 1 used for the ESO/Argo CD schemas). The raw URLs are:

- CNPG:
  `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/cluster_v1.json`
  `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/scheduledbackup_v1.json`
  `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/backup_v1.json`
- Redis:
  `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/databases.spotahome.com/redisfailover_v1.json`

CNPG is well-represented in the catalog under `postgresql.cnpg.io/` at `v1`
(the single served/stored version on the 1.24+ line), so the three CNPG files
are expected to resolve directly. **The Spotahome `redisfailover_v1.json` is the
one at risk** — the Spotahome operator is far less commonly catalogued than
CNPG, so its presence at `databases.spotahome.com/` in the catalog is **not
assumed**.

**Fallback — generate the schema from the operator's published CRD.** For any
kind the catalog does not publish at the needed `v1` (expected to be
`RedisFailover`, possibly a CNPG kind if the catalog lags the 1.24+ line),
derive the kubeconform-compatible JSON schema from the operator's own CRD
`spec.versions[].schema.openAPIV3Schema` using
[`openapi2jsonschema`](https://github.com/yannh/openapi2jsonschema) (the
tool kubeconform's own docs recommend for self-hosted CRD schemas):

1. Fetch the operator's CRD YAML at a **pinned ref** (not `latest`):
   - CNPG: the `cnpg-<ver>.yaml` install bundle / the `clusters.postgresql.cnpg.io`,
     `scheduledbackups.postgresql.cnpg.io`, `backups.postgresql.cnpg.io` CRDs
     from the CloudNativePG release matching the documented **1.24+** floor
     (`github.com/cloudnative-pg/cloudnative-pg` release assets).
   - Redis: the `redisfailovers.databases.spotahome.com` CRD from the pinned
     Spotahome `redis-operator` release
     (`github.com/spotahome/redis-operator`, `manifests/databases.spotahome.com_redisfailovers.yaml`).
2. Run `openapi2jsonschema` against the CRD to emit `<kind>_<version>.json`,
   then rename to the **canonical kind casing** the pinned kubeconform 0.8.0
   requires (`Cluster_v1.json`, `ScheduledBackup_v1.json`, `Backup_v1.json`,
   `RedisFailover_v1.json`) — 0.8.0 has no `toLower`, and the `-schema-location`
   template is `{{.ResourceKind}}_{{.ResourceAPIVersion}}.json`.

**Record provenance per file** in `crd-schemas/README.md`: for each of the four,
state whether it was **catalog-sourced** (with the exact raw URL above) or
**CRD-derived** (with the operator repo, the pinned release tag, the CRD
filename, and the `openapi2jsonschema` command). The implementer is therefore
never stuck: catalog first, operator CRD second, with the exact commands for
both. (If the catalog ships only a *looser* schema that does not deep-validate
the CR, the CRD-derived form is preferred for the kinds whose fields this repo
actually sets — `Cluster` and `RedisFailover` — so a typo in a set field is
caught by pass (b), not silently accepted.)

> **Kind-name collision check (the README's caveat):** the `-schema-location`
> template omits the API group and resolves by `kind`+version. New kinds
> `Cluster`, `ScheduledBackup`, `Backup`, `RedisFailover` do not collide with the
> existing `Application` / `ExternalSecret` / `ClusterSecretStore` across groups,
> so kind+version remains unambiguous — the group-omission stays safe. Record
> this in `deploy/k8s/crd-schemas/README.md` (extend the Files/apiVersions table
> and the provenance list: CNPG from the datreeio catalog
> `postgresql.cnpg.io/…`; RedisFailover from `databases.spotahome.com/…`, both
> renamed to canonical kind casing).

### Two-pass gate change

The `Cluster` and `ScheduledBackup` live in `deploy/k8s/base` → rendered into
**both** `dev.yaml` and `prod.yaml`; `RedisFailover` likewise (base). So the
CRD-bearing renders are still `dev.yaml` / `prod.yaml` (and `argocd.yaml`).
Changes to `pyproject.toml` `[tool.poe.tasks.k8s-check]`:

- **Pass (a) lenient:** unchanged in shape — `default` + the vendored-dir
  `-schema-location` template already resolve the new files by
  `{{.ResourceKind}}_{{.ResourceAPIVersion}}.json`. The new CRDs resolve here
  automatically once the JSON files exist.
- **Pass (b) durable "resolves, not skips":** the `-skip` core-kinds list is
  unchanged (it skips only built-in k8s kinds). Because pass (b) removes
  `-ignore-missing-schemas` and points `-schema-location` only at the vendored
  dir, the newly-rendered `Cluster` / `ScheduledBackup` / `RedisFailover`
  **MUST resolve to a vendored schema or the build fails** — which is exactly the
  intended guard. **No edit to the `-skip` list is needed** (the new kinds are
  CRDs, not core kinds, so they must NOT be skipped). The only required change is
  **adding the four JSON files**; the gate then enforces them automatically. Add
  a comment to the task noting the CNPG + Redis kinds are now among the CRDs pass
  (b) must resolve.
- **`CronJob` in the `-skip` list (review round-3 finding 3).** After this batch
  the backup `CronJob` is deleted and **no `CronJob` is rendered** in
  dev/prod/argocd. A `-skip` entry for an absent kind is harmless (skipping a
  kind that is not present is a no-op), so this is cosmetic, not a correctness
  issue. **Chosen: drop `CronJob` from the `-skip` list** in the same edit that
  adds the "CNPG + Redis kinds are now pass-(b)-resolved" comment, so the skip
  list has no dead entry an implementer later puzzles over. (If an implementer
  prefers to retain it defensively for consumer-added CronJobs, leave a one-word
  inline note saying so — either is acceptable; the design's preference is to
  drop it for a clean, self-explaining skip list.)
- kube-linter still runs on dev/prod only. Verify (do not assume) it emits zero
  new diagnostics against the CNPG `Cluster` + `RedisFailover` CRs; if a default
  check fires on an unknown CRD (unlikely — kube-linter targets workload kinds),
  add an exclusion with a written reason.

### Verification commands (run at implementation, from repo root)

1. `uv run poe k8s-check` — both kubeconform passes resolve the new CRDs; pass
   (b) fails if any CNPG/Redis schema is missing/mis-cased; kube-linter clean.
2. `kubectl kustomize deploy/k8s/overlays/dev` and `.../prod` render the
   `Cluster`, `ScheduledBackup`, `RedisFailover` with the correct per-env Barman
   path and namespace.
3. `uv run poe check` (lint + mypy --strict + unit tests) — new `cache.py` /
   `config.py` / `db.py` code + `test_cache.py` / updated `test_config.py` /
   `test_db.py` pass with NO services.
4. `uv run poe security` (gitleaks + ruff S + sops guard) green.
5. `uv run poe docs-build` (mkdocs --strict) passes after doc edits.
6. `rg PLACEHOLDER_` lists only intended tokens (BACKUP_IMAGE + DB_PASSWORD gone;
   DB_STORAGE_SIZE/CLASS + BACKUP_PREFIX added).
7. `python -c "import yaml; yaml.safe_load(...)"` + `pre-commit run check-yaml`
   on `sync-secrets.yml` (actionlint not installed locally).

---

## 5. Files to create / modify / delete

### Create

| File                                             | Purpose                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deploy/k8s/base/postgres-cluster.yaml`          | CNPG `Cluster` CR: 3 instances, quorum `any 1` sync, anti-affinity, resources, `enableSuperuserAccess: false`, `storage` (PLACEHOLDER size/class), `bootstrap.initdb` (db `app`/role `app`), `serviceAccountTemplate` IRSA (`PLACEHOLDER_BACKUP_ROLE_ARN`), `backup.barmanObjectStore` → `s3://PLACEHOLDER_BACKUP_BUCKET/PLACEHOLDER_BACKUP_PREFIX`, `retentionPolicy "30d"`.                                         |
| `deploy/k8s/base/scheduledbackup.yaml`           | CNPG `ScheduledBackup`, daily 03:00 UTC (6-field cron), `cluster.name: pg`.                                                                                                                                                                                                                                                                                                                                           |
| `deploy/k8s/base/redis-failover.yaml`            | Spotahome `RedisFailover`: 3 sentinels, 3 redis, resources/securityContext, `auth.secretPath: app-secrets` (key `REDIS_PASSWORD`).                                                                                                                                                                                                                                                                                    |
| `deploy/k8s/crd-schemas/Cluster_v1.json`         | Vendored CNPG `Cluster` schema.                                                                                                                                                                                                                                                                                                                                                                                       |
| `deploy/k8s/crd-schemas/ScheduledBackup_v1.json` | Vendored CNPG `ScheduledBackup` schema.                                                                                                                                                                                                                                                                                                                                                                               |
| `deploy/k8s/crd-schemas/Backup_v1.json`          | Vendored CNPG `Backup` schema (completeness).                                                                                                                                                                                                                                                                                                                                                                         |
| `deploy/k8s/crd-schemas/RedisFailover_v1.json`   | Vendored Spotahome `RedisFailover` schema.                                                                                                                                                                                                                                                                                                                                                                            |
| `tests/unit/test_cache.py`                       | Unit tests: `parse_sentinel_hosts` + `create_redis` mode selection (mocked, no services).                                                                                                                                                                                                                                                                                                                             |
| `tests/unit/test_db.py`                          | Unit test: `create_engine` scheme normalization (`postgresql://` → `postgresql+asyncpg://`), already-`+asyncpg` untouched, a libpq `?sslmode=require` URL raises `ValueError` **naming `sslmode`**, **and a non-libpq query param (`?prepared_statement_cache_size=0`, bare `?ssl=disable`) is ACCEPTED unchanged** (parameter-specific guard, not a blanket `?` ban — review round-3 finding 1). String-only, no DB. |

### Modify

| File                                            | Change                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/app/config.py`                             | `database_url`→optional; add `pg_host/pg_port/pg_user/pg_password/pg_dbname` + `database_dsn` property; `redis_url`→optional; add `redis_sentinel_hosts`, `redis_master_name` (default `mymaster`), `redis_password`; `model_validator` requiring one DB mode **and** one Redis mode; `redis_is_sentinel` property.                                                                                                      |
| `src/app/cache.py`                              | `create_redis(settings: Settings)`; Sentinel branch via `Sentinel(...).master_for(...)` passing `decode_responses=True`; plain branch via `from_url`; add typed `parse_sentinel_hosts`.                                                                                                                                                                                                                                  |
| `src/app/db.py`                                 | `create_engine` normalizes `postgresql://`→`postgresql+asyncpg://` **and** raises `ValueError` naming the offender **only** for the known-bad libpq query params (`sslmode`/`sslrootcert`/`sslcert`/`sslkey`/`sslpassword`/`gssencmode`/`sslnegotiation`) — NOT a blanket `?` ban, so valid asyncpg query params and a bare `ssl=` pass through (review round-3 finding 1).                                              |
| `src/app/main.py`                               | lifespan: `create_engine(resolved.database_dsn)` and `create_redis(resolved)` (were `…database_url` / `create_redis(resolved.redis_url)`).                                                                                                                                                                                                                                                                               |
| `migrations/env.py`                             | **HIGH (review finding 1):** `get_url()` returns `Settings().database_dsn` (was `Settings().database_url`); keeps `-> str`. Without this the PreSync migration hook breaks in-cluster (no `DATABASE_URL`, only `PG_*`) and `mypy --strict` fails. Ships in **commit 1**.                                                                                                                                                 |
| `deploy/k8s/base/kustomization.yaml`            | Remove `backup-serviceaccount.yaml`, `backup-cronjob.yaml`; add `postgres-cluster.yaml`, `scheduledbackup.yaml`, `redis-failover.yaml`.                                                                                                                                                                                                                                                                                  |
| `deploy/k8s/base/configmap.yaml`                | Add `REDIS_SENTINEL_HOSTS: "rfs-redis:26379"` and `REDIS_MASTER_NAME: "mymaster"`.                                                                                                                                                                                                                                                                                                                                       |
| `deploy/k8s/base/deployment.yaml`               | Project discrete CNPG `pg-app` keys as env `PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DBNAME` via `secretKeyRef` (NOT `uri`, to avoid libpq `sslmode`); `app-secrets` `envFrom` kept (for `REDIS_PASSWORD`).                                                                                                                                                                                                                |
| `deploy/k8s/base/migration-job.yaml`            | Same discrete `PG_*` sourcing change; comment that migrations run against `pg-rw` (the CNPG primary).                                                                                                                                                                                                                                                                                                                    |
| `deploy/k8s/base/networkpolicy.yaml`            | Egress: keep 5432/6379, add 26379 (Sentinel); remove 443 **and** all three backup-pod clauses (top description, inline 443 comment, TIGHTEN-example); rewrite header + tighten-example to CNPG/Redis Service labels.                                                                                                                                                                                                     |
| `deploy/k8s/.kube-linter.yaml`                  | **MEDIUM (review finding 2):** both current exclusions cite the deleted `backup-cronjob.yaml`. Re-run kube-linter on the CronJob-deleted renders: remove `latest-tag` if it no longer fires (likely dead — untagged image gone); rewrite the `job-ttl-seconds-after-finished` reason to cite the **migration Job** (still sets `ttlSecondsAfterFinished: 3600`), or remove if it no longer fires. Ships in **commit 3**. |
| `deploy/k8s/overlays/dev/kustomization.yaml`    | Remove `db-backup` `BACKUP_PREFIX` patch; **MANDATORY** add patch setting CNPG `Cluster` Barman `destinationPath`/`serverName` to `dev`.                                                                                                                                                                                                                                                                                 |
| `deploy/k8s/overlays/prod/kustomization.yaml`   | **MANDATORY** same patch swap → `prod` (distinct prefix); optionally patch CNPG/Redis resources upward.                                                                                                                                                                                                                                                                                                                  |
| `deploy/k8s/overlays/dev/externalsecret.yaml`   | **Structural change:** `dataFrom.extract` → explicit `data:` with two keys (`REDIS_PASSWORD` for app, `password` for the Spotahome operator) both from the one SM property `REDIS_PASSWORD`.                                                                                                                                                                                                                             |
| `deploy/k8s/overlays/prod/externalsecret.yaml`  | Same structural change (two keys `REDIS_PASSWORD` + `password` from one SM property).                                                                                                                                                                                                                                                                                                                                    |
| `deploy/k8s/overlays/dev/secrets.example.yaml`  | `stringData`: only `REDIS_PASSWORD: PLACEHOLDER_REDIS_PASSWORD`.                                                                                                                                                                                                                                                                                                                                                         |
| `deploy/k8s/overlays/prod/secrets.example.yaml` | Same.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `.github/workflows/sync-secrets.yml`            | Key-set assertion `{DATABASE_URL, REDIS_URL}` → `{REDIS_PASSWORD}` at ALL seven hardcoded sites (header, `expected`, extra-key filter, missing-key loop, two error messages, success echo) — see §F finding-3 enumeration.                                                                                                                                                                                               |  |
| `scripts/bootstrap_backups.py`                  | Docstring: object layout is now Barman (`<prefix>/base`, `/wals`), not `db/<ts>.sql.gz`; retention still 30d; no behavioral change; tests unaffected.                                                                                                                                                                                                                                                                    |
| `pyproject.toml`                                | `k8s-check`: comment that CNPG + Redis CRDs are now pass-(b)-resolved; **drop the now-dead `CronJob` entry from the pass-(b) `-skip` list** (no `CronJob` is rendered after the backup CronJob is deleted — review round-3 finding 3). No other `-skip` change.                                                                                                                                                          |
| `deploy/k8s/crd-schemas/README.md`              | Extend Files/apiVersions table + **per-file provenance** for the four new schemas — for each, state catalog-sourced (with the exact `datreeio/CRDs-catalog` raw URL) **or** CRD-derived (operator repo, pinned release tag, CRD filename, `openapi2jsonschema` command) per §4's named sources + fallback (review round-3 finding 2); restate kind-collision safety.                                                     |
| `tests/unit/test_config.py`                     | Neither DB nor Redis individually required; **replace** `test_missing_required_field_raises` with DB + Redis mode-matrix cases; add `database_dsn` composition/quoting test.                                                                                                                                                                                                                                             |
| `tests/conftest.py`                             | `placeholder_env` keeps plain `REDIS_URL` (local mode) — comment why.                                                                                                                                                                                                                                                                                                                                                    |
| `tests/integration/test_items_api.py`           | Docstring note: integration uses plain Redis; Sentinel covered by mocked unit test.                                                                                                                                                                                                                                                                                                                                      |
| `.env.example`                                  | Keep `DATABASE_URL` + `REDIS_URL` (both already carry `local-only-password`) for local dev plain mode. Add a **commented, explicitly mutually-exclusive** cluster-only Sentinel block (review finding 4) — concrete block specified in §D; it warns that uncommenting switches to Sentinel mode and `REDIS_URL` must be removed. Note in-cluster `DATABASE_URL` is composed from CNPG's `PG_*` keys (not set here).      |
| `.template/placeholders.yml`                    | See §6: token delta; add `docs/configuration.md` to `PROJECT_SLUG.replace_in`; rewrite the NetworkPolicy label-coupling NOTE (drop backup-pod clause, add operator-labeled data plane).                                                                                                                                                                                                                                  |
| `docs/adr/0001-technology-choices.md`           | Dated reversal note + Decision summary update (in place, no new ADR).                                                                                                                                                                                                                                                                                                                                                    |
| `SPEC.md`                                       | §1/§2/§3/§5 (config table **and** the §5.1 `DATABASE_URL`/`REDIS_URL`-in-SOPS sentence — review finding 3)/§7 (incl. the `migrations/env.py` reads-`database_dsn` line — review finding 1)/§9/§10/§13 edits per §C.                                                                                                                                                                                                      |
| `docs/deployment.md`                            | CNPG + Redis operators assumed pre-installed (CNPG 1.24+ floor); CNPG Barman backups replace the CronJob; the first-boot ordering rough edge (migration retries until CNPG primary ready) next to the ESO first-sync note.                                                                                                                                                                                               |
| `docs/architecture.md`                          | "Kubernetes shape": CNPG Cluster (3 instances) + Redis Sentinel replace external URL endpoints.                                                                                                                                                                                                                                                                                                                          |
| `docs/secrets.md`                               | `app-secrets` key set now `{REDIS_PASSWORD}`; `DATABASE_URL` comes from CNPG's generated Secret.                                                                                                                                                                                                                                                                                                                         |
| `docs/configuration.md`                         | Config table: Redis Sentinel vars; `DATABASE_URL` vs discrete `PG_*` source (CNPG). Add the slug-bearing Secrets Manager id example `<slug>/<env>/app-secrets` (now covered by `PROJECT_SLUG.replace_in`).                                                                                                                                                                                                               |

### Delete

| File                                         | Reason                                                                      |
| -------------------------------------------- | --------------------------------------------------------------------------- |
| `deploy/k8s/base/backup-cronjob.yaml`        | Replaced by CNPG-native Barman `ScheduledBackup`.                           |
| `deploy/k8s/base/backup-serviceaccount.yaml` | CNPG uses its own SA; IRSA moves to the `Cluster` `serviceAccountTemplate`. |

---

## 6. PLACEHOLDER_ token delta

**Removed**

- `PLACEHOLDER_BACKUP_IMAGE` — no `pg_dump` container image; CNPG uses the
  operator's Postgres image. Delete the `fill` entry.
- `PLACEHOLDER_DB_PASSWORD` — the DB password is CNPG-generated; it no longer
  appears in any file (the example secret drops `DATABASE_URL`). Delete the
  `fill` entry.

**Added (new `fill` entries)**

- `PLACEHOLDER_DB_STORAGE_SIZE` — CNPG `Cluster` `spec.storage.size` (e.g.
  `10Gi`). File: `deploy/k8s/base/postgres-cluster.yaml`.
- `PLACEHOLDER_DB_STORAGE_CLASS` — CNPG `Cluster` `spec.storage.storageClass`.
  File: `deploy/k8s/base/postgres-cluster.yaml`.
- `PLACEHOLDER_BACKUP_PREFIX` — Barman `destinationPath`/`serverName` prefix
  segment in base. File: `deploy/k8s/base/postgres-cluster.yaml`. (A valid plain
  string so base renders.) **MANDATORY per-overlay patch (review finding 11):**
  both `overlays/dev` and `overlays/prod` MUST patch the `Cluster`'s
  `spec.backup.barmanObjectStore.destinationPath` (and `serverName`) to a
  distinct per-env prefix (`dev`/`prod`), exactly as the old `BACKUP_PREFIX`
  CronJob patch was mandatory. Distinct prefixes keep dev/prod WAL and base
  backups from colliding in the shared bucket. This is a required patch, not an
  optional one; if an overlay omits it, the literal `PLACEHOLDER_BACKUP_PREFIX`
  ships (and `rg PLACEHOLDER_` after init catches it only if the token survives
  verbatim). The Modify table flags both overlay kustomizations accordingly.

**Kept, `files:` updated**

- `PLACEHOLDER_BACKUP_BUCKET` — `files:` → `deploy/k8s/base/postgres-cluster.yaml`
  + `scripts/bootstrap_backups.py` (was `backup-cronjob.yaml`).
- `PLACEHOLDER_BACKUP_KMS_KEY_ID` — `files:` → `deploy/k8s/base/postgres-cluster.yaml`
  + `scripts/bootstrap_backups.py` (was `backup-cronjob.yaml`); description note
  that CNPG inherits the bucket's default SSE-KMS CMK.
- `PLACEHOLDER_BACKUP_KMS_ALIAS` — unchanged (`scripts/bootstrap_backups.py`).
- `PLACEHOLDER_BACKUP_ROLE_ARN` — `files:` → `deploy/k8s/base/postgres-cluster.yaml`
  (CNPG `serviceAccountTemplate` IRSA annotation) (was `backup-serviceaccount.yaml`).
- `PLACEHOLDER_REDIS_PASSWORD` — unchanged (still the example secret), now the
  only key in `secrets.example.yaml`.

**`PROJECT_SLUG.replace_in` additions (extend, do not drop existing entries)**

The `deploy/k8s/**` glob already covers the new base manifests
(`postgres-cluster.yaml`, `scheduledbackup.yaml`, `redis-failover.yaml`). The
following edits are **concrete and unconditional** (review finding 7), not
"confirm/if":

1. **Add `docs/configuration.md` to `PROJECT_SLUG.replace_in`.** It is not
   currently listed. After this batch it carries a slug-bearing occurrence: the
   Secrets Manager id example `<slug>/<env>/app-secrets` (added when
   `DATABASE_URL`/`REDIS_URL` are replaced with the CNPG + Sentinel contract).
   `docs/secrets.md` and `docs/deployment.md` are already in `replace_in`
   (Secrets Manager id) and stay.
2. **Rewrite the NetworkPolicy label-coupling NOTE** in `placeholders.yml` (do
   not merely tweak it). The current NOTE says the backup CronJob pod
   (`deploy/k8s/base/backup-cronjob.yaml`) carries `app.kubernetes.io/name` and
   that renaming piecemeal makes "the backup pod … silently lose egress." That
   file is **deleted** this batch, so that clause is false. New NOTE text:

   > NOTE (NetworkPolicy label coupling — rename these as a SET):
   > `deploy/k8s/base/networkpolicy.yaml` selects pods by
   > `app.kubernetes.io/name: <slug>`. The application pods
   > (`deployment.yaml` / `migration-job.yaml`) carry that label and are the
   > only pods this policy governs. The in-cluster data plane — the CNPG
   > `Cluster` pods and the `RedisFailover` redis/sentinel pods — are created
   > and **labeled by their operators** (e.g. `cnpg.io/cluster`,
   > `app.kubernetes.io/managed-by`), NOT `app.kubernetes.io/name: <slug>`, so
   > they are not selected by this policy and renaming the slug never touches
   > them. The NetworkPolicy's own podSelector/matchLabels must still change
   > together with the app/migration pod labels; the `deploy/k8s/**` glob covers
   > all `app.kubernetes.io/name` occurrences in one pass — just do not rename
   > them piecemeal.

   The old "backup pod silently loses egress" clause is removed entirely.

---

## 7. Commit split (Conventional Commits, feeds git-cliff)

Ordered so each commit builds + passes `uv run poe check` / `k8s-check` where
applicable (template stays green at every step):

1. **`feat(cache)!: build Redis/DB clients from Sentinel+CNPG config, keep plain URLs for local dev`**
   — `config.py` (DB two-mode + Redis two-mode validator, `database_dsn`),
   `cache.py` (Sentinel), `db.py` (scheme normalize + query-string guard),
   `main.py` (`create_engine(resolved.database_dsn)`, `create_redis(resolved)`),
   **`migrations/env.py`** (`get_url()` → `database_dsn`, review finding 1);
   `tests/unit/test_cache.py`, `tests/unit/test_db.py`, updated
   `test_config.py`, `conftest.py`, `.env.example`. (`!` — config contract
   change.) Verified by `uv run poe check` (lint + mypy --strict + unit tests —
   including that the Alembic env module typechecks and imports cleanly).
2. **`feat(k8s): add CloudNativePG Cluster + ScheduledBackup and Redis Sentinel (RedisFailover)`**
   — the three new base manifests, `configmap.yaml` Sentinel vars,
   `deployment.yaml`/`migration-job.yaml` discrete `PG_*`-from-CNPG env,
   kustomization wiring; the four vendored CRD schemas + `crd-schemas/README.md`;
   `k8s-check` comment. Verified by `uv run poe k8s-check`.
3. **`refactor(k8s): replace pg_dump CronJob with CNPG Barman backups`**
   — delete `backup-cronjob.yaml` + `backup-serviceaccount.yaml`; CNPG
   `barmanObjectStore` + `ScheduledBackup` (added in commit 2 or here —
   kept here for a clean "removal" diff); networkpolicy egress rework;
   overlay patch swap (`BACKUP_PREFIX` CronJob patch → CNPG Barman path);
   `bootstrap_backups.py` docstring; **`.kube-linter.yaml` stale-exclusion
   reconciliation** (review finding 2). Verified by `uv run poe k8s-check`.
4. **`feat(secrets): source DB creds from CNPG; materialize app-secrets REDIS_PASSWORD + password for the Redis operator`**
   — `externalsecret.yaml` (both — explicit two-key `data:` block),
   `secrets.example.yaml` (both — only `REDIS_PASSWORD`), `sync-secrets.yml`
   key-set assertion at all seven sites, `RedisFailover` `auth.secretPath`
   reads the `password` key. Verified by `uv run poe security` + `k8s-check`.
5. **`chore(template): update placeholders for CNPG storage + Barman; drop pg_dump image/db-password tokens`**
   — `.template/placeholders.yml` token delta + `replace_in` additions +
   label-coupling NOTE update. Verified by `rg PLACEHOLDER_`.
6. **`docs: adopt in-cluster HA Postgres/Redis; reverse SPEC §2 non-goal in ADR 0001`**
   — `SPEC.md` (§1/§2/§3/§5/§7/§9/§10/§13), `docs/adr/0001-technology-choices.md`
   (dated reversal note), `docs/deployment.md`, `docs/architecture.md`,
   `docs/secrets.md`, `docs/configuration.md`. Verified by `uv run poe docs-build`.

(Commits 2–4 may be squashed if a reviewer prefers a single manifest change, but
the split keeps the backup *removal* isolated and reviewable.)

---

## 8. Testability summary

- **Unit (no services):** DB + Redis mode selection + validators; `database_dsn`
  composition/quoting; `parse_sentinel_hosts`; `create_redis` branch selection
  (mocked `Sentinel`/`from_url`, asserting `decode_responses=True` reaches the
  Sentinel path); `create_engine` scheme normalization, the **parameter-specific**
  libpq `ValueError` guard (rejects `sslmode` et al. by name), **and** that a
  non-libpq query param / bare `ssl=` is accepted unchanged (review round-3
  finding 1); existing `bootstrap_backups` plan/guard tests unchanged.
- **Integration (`@pytest.mark.integration`, Docker Compose plain Redis):**
  existing items CRUD + `/readyz` against plain local Redis + Postgres — unchanged
  and still green; Sentinel wiring is NOT exercised locally by design (local dev
  is plain Redis), and the Sentinel construction path is covered by the mocked
  unit test.
- **Manifest (CI `k8s-check`):** two-pass kubeconform now enforces the four new
  CRD schemas; kube-linter on dev/prod verified clean (exclusion-with-reason only
  if a check actually fires).
- **Not unit-testable (integration/manual by nature):** CNPG failover,
  Sentinel master election, Barman→S3 round-trip, Argo CD first-boot ordering —
  documented as operational/rough-edge behavior, not covered by the template's
  automated tests.

---

## 9. Responses to the design reviews

### Round 1 (`design-review-ha.md` round-1 findings)

Verdict was CHANGES_REQUESTED (2 HIGH, 5 MEDIUM, 4 NIT). Every finding is
addressed below.

1. **HIGH — `auth.secretPath` key name (`password`, not `REDIS_PASSWORD`).**
   *Addressed.* Verified: the Spotahome operator reads the key **`password`**
   from the Secret named in `spec.auth.secretPath`. §B and §F now state the
   `RedisFailover` consumes `password`, while the app consumes `REDIS_PASSWORD`,
   and the ESO `ExternalSecret` materializes **both** keys from the single
   Secrets Manager property `REDIS_PASSWORD` (explicit two-entry `data:` block)
   so they are guaranteed identical — the no-drift claim holds and is now
   correct. Modify table + commit 4 updated.

2. **HIGH — CNPG `uri` may carry `sslmode`/libpq params asyncpg rejects.**
   *Addressed.* Confirmed empirically (`create_async_engine(…?sslmode=require)`
   → `TypeError: connect() got an unexpected keyword argument 'sslmode'`). The
   mechanism in §F changed from "feed CNPG's `uri` with a scheme-only rewrite"
   to "project the **discrete** CNPG keys (`host`/`port`/`username`/`password`/
   `dbname`) and compose a query-string-free DSN in `config.database_dsn`."
   `db.py` additionally normalizes the scheme and raises `ValueError` on any
   libpq query string (guarding the local-dev `.env` path), with a dedicated
   `test_db.py` case.

3. **MEDIUM — `sync-secrets.yml` key-set change under-specified.**
   *Addressed.* §F enumerates all seven hardcoded `DATABASE_URL`/`REDIS_URL`
   sites and their exact replacements; states the single-key `keys | sort`
   comparison is now sufficient and the per-key loop is kept (reduced) only for
   a specific error message.

4. **MEDIUM — `test_missing_required_field_raises` encodes a false contract.**
   *Addressed.* §D now **replaces** that parametrized test with explicit DB and
   Redis mode-matrix cases (each mode set alone passes; neither →
   `ValidationError`) plus a `database_dsn` composition test, because after the
   change neither `DATABASE_URL` nor `REDIS_URL` is individually required.

5. **MEDIUM — CNPG Secret name/keys asserted, bootstrap must not supply a secret.**
   *Addressed.* §A and §F now state explicitly that the `Cluster` **omits**
   `spec.bootstrap.initdb.secret` (so CNPG generates `pg-app`), list the
   generated keys, and note the design depends only on the stable discrete keys
   (not `uri`); the exact set is confirmed against CNPG 1.24+ at implementation.

6. **MEDIUM — base ConfigMap Sentinel vars / dev overlay mode ambiguity.**
   *Addressed.* §F states both cluster overlays (dev and prod) are Sentinel-mode
   (both deploy a `RedisFailover`), that the ConfigMap/overlays carry no
   `REDIS_URL` (so no pod has both modes set), and that local Docker Compose is
   unaffected because it reads `.env`.

7. **MEDIUM — `PROJECT_SLUG.replace_in` + NetworkPolicy NOTE deferred.**
   *Addressed.* §6 now makes `docs/configuration.md` an **unconditional**
   `replace_in` addition and gives the **rewritten** label-coupling NOTE text
   (drops the deleted backup-pod clause; adds that CNPG/`RedisFailover` pods are
   operator-labeled, not slug-labeled).

8. **NIT — NetworkPolicy header + TIGHTEN-example still reference the backup pod.**
   *Addressed.* §E lists removal of **all three** backup clauses (top
   description, inline 443 comment, TIGHTEN-example), not just the header.

9. **NIT — `decode_responses=True` propagation on the Sentinel path.**
   *Addressed.* §D adds the propagation note and an explicit `test_cache.py`
   assertion that the Sentinel constructor receives `decode_responses=True`.

10. **NIT — 6-field cron for `ScheduledBackup`.**
    *Addressed.* §E requires an inline comment stating the schedule is CNPG's
    6-field (seconds-leading) format, intentional, not 5-field.

11. **NIT — `PLACEHOLDER_BACKUP_PREFIX` overlay override not enforced.**
    *Addressed.* §6 and the Modify table flag the per-overlay Barman
    `destinationPath`/`serverName` patch as **MANDATORY** (distinct dev/prod
    prefixes), mirroring the old mandatory `BACKUP_PREFIX` patch.

The review's "Unverified / Wrong Assumptions" that carried into implementation
(CNPG `enableSuperuserAccess` default, kube-linter cleanliness on the CRs,
datreeio catalog availability of the CNPG/Spotahome schemas) are called out
in-line as "verify at implementation, do not assume," and `enableSuperuserAccess:
false` is now set **explicitly** in the `Cluster` rather than relied on as a
default (§A).

### Round 2 (`design-review-ha.json` / `design-review-ha.md` round-2 findings)

Verdict CHANGES_REQUESTED (1 HIGH, 3 MEDIUM, 2 NIT). All six addressed; each was
verified against the repo first.

1. **HIGH — `migrations/env.py` still reads `Settings().database_url`; the design
   never touched it.** *Addressed.* Verified in the repo: `get_url() -> str`
   returns `Settings().database_url`, and the migration Job gets only `PG_*`
   in-cluster — so under the new optional-`database_url` contract the PreSync
   hook would get `None` at runtime **and** `mypy --strict` would fail (`-> str`
   vs `str | None`). `migrations/env.py` is now in the "Current state" list, the
   Modify table (changing `get_url()` to return `database_dsn`, keeping
   `-> str`), §F (the one-line fix with both the offline and async-online
   consequences), and **commit 1** (so `uv run poe check` genuinely passes). The
   §C SPEC §7 edit now also updates the "`env.py` reads `DATABASE_URL`" line to
   `database_dsn`.

2. **MEDIUM — `.kube-linter.yaml` exclusions are justified by the deleted
   `backup-cronjob.yaml`.** *Addressed.* Verified: both exclusions
   (`latest-tag`, `job-ttl-seconds-after-finished`) carry §14.9 reasons citing
   the backup CronJob, and the migration Job also sets
   `ttlSecondsAfterFinished: 3600`. Added `deploy/k8s/.kube-linter.yaml` to the
   Modify table and a dedicated §E subsection: re-run kube-linter on the
   CronJob-deleted renders, **remove** `latest-tag` if it no longer fires (likely
   dead), and **rewrite** the `job-ttl` reason to cite the migration Job (or
   remove if it no longer fires). Ships in commit 3.

3. **MEDIUM — SPEC §5.1 hardcodes "`DATABASE_URL`/`REDIS_URL` stay in the SOPS
   Secret unchanged."** *Addressed.* Added SPEC §5.1 as an explicit entry in the
   §C edit list, separate from the §5 config-table edit, with the exact rewrite
   (Redis password in SOPS as `REDIS_PASSWORD`; Postgres credential from CNPG's
   `pg-app` Secret, not SOPS) while keeping the "DD config is non-secret" point.

4. **MEDIUM — `.env.example` cluster-Sentinel block + "both set" precedence
   under-specified.** *Addressed.* Verified `.env.example` already carries the
   `local-only-password` on `REDIS_URL` (matching Compose), so local dev is not
   broken; the gap was the unspecified commented block and the silent
   Sentinel-wins precedence. §D now gives the **concrete** `.env.example` block
   (cluster vars commented out, declared mutually exclusive with `REDIS_URL`,
   with the "remove `REDIS_URL` if you uncomment these" warning) and states that
   the Sentinel-hosts-wins precedence is intentional. The Modify-table row is
   updated to point at that block.

5. **NIT — `sync-secrets.yml` slug references are a separate concern from the
   seven key-set sites.** *Addressed.* Added a scope note to §F: the seven sites
   are key-set strings only; the `k8s-fastapi-starter` slug in `SECRET_ID`/header
   is a `PROJECT_SLUG` concern (already covered by `replace_in`) and is left
   alone in the key-set edits.

6. **NIT — `redis_master_name` "non-empty" claimed in the table but not enforced
   in the class.** *Addressed.* Chose to **enforce** (not drop the claim):
   `redis_master_name: str = Field(default="mymaster", min_length=1)`, so an
   explicit `REDIS_MASTER_NAME=""` is rejected at startup. The validation table
   row and a new `test_config.py` case (`REDIS_MASTER_NAME=""` →
   `ValidationError`) are updated to match, and the mypy note records the
   `pydantic.Field` import.

**Round-2 "Unverified / Wrong Assumptions" (carried, not blocking).** The review
re-listed the same operator-dependent facts the design already flags as
"verify at implementation, do not assume": the exact CNPG `pg-app` generated key
names (pin `username`/`password`/`host`/`port`/`dbname` against the shipped CNPG
1.24+ line before writing the `secretKeyRef` wiring), that omitting
`bootstrap.initdb.secret` makes CNPG *generate* `pg-app`, the Spotahome
`auth.secretPath` key name `password`, that `barmanObjectStore` with
`inheritFromIAMRole: true` picks up the `serviceAccountTemplate` IRSA role and
the bucket-default SSE-KMS suffices, the datreeio catalog availability of the
four CNPG/Spotahome schemas, kube-linter emitting zero new diagnostics on the
CRs, and the CNPG 6-field cron. These remain implementation-time verifications
with the mitigations already stated in §A/§B/§E/§4; none changes a design
decision.

### Round 3 (`design-review-ha.md` round-3 findings)

Verdict CHANGES_REQUESTED (0 HIGH, 2 MEDIUM, 2 NIT). All four addressed; none
reopens a settled decision (CNPG, Redis Sentinel, SPEC reversal, pg_dump CronJob
removal all stand).

1. **MEDIUM — `db.py` query-string guard can reject a legitimate local-dev DSN.**
   *Addressed.* The guard is changed from a **blanket `?` ban** to a
   **parameter-specific** check: §F's `db.py` paragraph, the §D validation table
   `DATABASE_URL` row, the invariant-ownership paragraph, and the Modify-table
   `src/app/db.py` row now all specify that `create_engine` parses the query and
   rejects **only** the known-bad libpq keys
   (`sslmode`/`sslrootcert`/`sslcert`/`sslkey`/`sslpassword`/`gssencmode`/`sslnegotiation`),
   raising a `ValueError` that **names the offending parameter**, and leaves
   every other query param (including a bare `ssl=` and
   `prepared_statement_cache_size`) untouched. The `test_db.py` spec is updated:
   case (c) now asserts the `sslmode` message names the param, and a **new case
   (d)** pins that a non-libpq query param / bare `?ssl=disable` is **accepted
   unchanged** — so a valid local DSN is no longer a false positive and the
   "local dev unchanged" promise holds. The blanket-`?` wording is removed
   everywhere it appeared.

2. **MEDIUM — Four vendored CRD schemas are an unverified external dependency
   that gates the whole manifest batch.** *Addressed.* §4 now names the **exact
   `datreeio/CRDs-catalog` raw URLs** for all four kinds (three CNPG +
   `redisfailover_v1.json`), flags the **Spotahome `RedisFailover` as the
   at-risk one**, and specifies an explicit **CRD-derived fallback**: fetch the
   operator's CRD at a **pinned release ref** (CloudNativePG release assets for
   CNPG at the 1.24+ floor; `spotahome/redis-operator`
   `manifests/databases.spotahome.com_redisfailovers.yaml` for Redis) and run
   `openapi2jsonschema` against `spec.versions[].schema.openAPIV3Schema`, then
   rename to canonical kind casing. The `crd-schemas/README.md` Modify row now
   requires **per-file provenance** (catalog URL vs. operator repo + pinned tag +
   command). The implementer is therefore never blocked at `k8s-check`: catalog
   first, operator CRD second, with exact commands for both.

3. **NIT — `pyproject.toml` `k8s-check` `-skip` list still names `CronJob`.**
   *Addressed.* The two-pass gate text and the `pyproject.toml` Modify row now
   **drop the dead `CronJob` entry** from the pass-(b) `-skip` list (no `CronJob`
   is rendered after the backup CronJob is deleted), in the same edit that adds
   the "CNPG + Redis kinds are now pass-(b)-resolved" comment — with a note that
   retaining it defensively with a one-word inline comment is an acceptable
   alternative.

4. **NIT — `database_dsn` does not percent-quote the username.** *Addressed.*
   The §D `database_dsn` code block now percent-quotes **both** `pg_user` and
   `pg_password` (`quote(..., safe="")`), with an inline comment explaining the
   CNPG role name is DNS-label-safe today but the composer stays correct for any
   role name. The `test_config.py` `database_dsn` composition-test spec is
   extended to include a URL-reserved char in the user/password to prove the
   quoting.

**Round-3 "Unverified / Wrong Assumptions" (carried, not blocking).** The review
re-listed the standing operator-dependent facts (CNPG `pg-app` key names,
`bootstrap.initdb.secret` omission → generated Secret, the Spotahome
`auth.secretPath` `password` key, `barmanObjectStore` IRSA/SSE-KMS behavior,
`enableSuperuserAccess: false` + app-role DDL privilege, kube-linter on the CRs,
catalog schema availability, `decode_responses` propagation, and the CNPG
6-field cron). All remain implementation-time verifications with the mitigations
already in §A/§B/§E/§4 and the finding-2 CRD-derived fallback; none changes a
design decision.


---

## Orchestrator addendum (design loop closed after round 4)

The orchestrator ended the design loop as **COMPLETED** after 4 iterations even
though the round-4 review verdict was CHANGES_REQUESTED. Rationale: every HIGH
finding and every architectural MEDIUM from rounds 1–3 is resolved and verified
against source; the round-4 verdict carried **0 HIGH** and only cosmetic
findings. This design is authoritative — build from it. All settled decisions
remain fixed (CloudNativePG; Redis Sentinel via Spotahome RedisFailover reading
the `password` key; SPEC §2 managed-DB/cache non-goal reversal recorded in ADR
0001 in place; pg_dump CronJob replaced by CNPG-native Barman `ScheduledBackup`
reusing the same S3 bucket/CMK; `DATABASE_URL` composed from CNPG `pg-app`
discrete keys; Redis two-mode config — plain for local dev, Sentinel in-cluster).

Carry these FOUR round-4 trailing findings forward as explicit **must-fix**
items during implementation (they are copy-edits, not design changes; the
build-loop semantic review must confirm them):

1. **(MEDIUM, cosmetic)** `pyproject.toml` `k8s-check` task comments say "the
   three vendored CRDs". After this batch there are more (CNPG `Cluster`,
   `ScheduledBackup`, `Backup`, plus Redis `RedisFailover`, on top of
   `Application`/`ExternalSecret`/`ClusterSecretStore`). Update the comment
   counts so they are not stale.
2. **(MEDIUM, cosmetic)** The "drop the deleted CronJob from the pass-(b)
   `-skip` list" instruction appears as two contradictory phrasings. Resolve to
   ONE unambiguous decision: **drop the dead CronJob `-skip` entry**, since
   `backup-cronjob.yaml` is deleted this batch.
3. **(NIT)** The CRD-schemas README should note that `Backup_v1.json` is
   vendored but not rendered by any committed manifest (only `ScheduledBackup`
   is rendered; `Backup` is the CNPG on-demand kind), so a future reader knows
   why it is present but unreferenced.
4. **(NIT)** `migrations/env.py` module docstring should reflect the
   `get_url()` change (now returns `Settings().database_dsn`, not
   `database_url`).
