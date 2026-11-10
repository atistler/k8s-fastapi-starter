# Implementation Plan: In-cluster HA Postgres (CloudNativePG) + Redis Sentinel

Source of truth: `.agents/tasks/k8s-fastapi-starter-cnpg-redis-sentinel-ha/design-ha.md`
(APPROVED). This plan sequences that design faithfully; it does not re-decide the
architecture. Work on branch `main` in the repo ROOT (no worktree). Follow
AGENTS.md: use `uv run poe <task>` (local == CI), Conventional Commits, never
hand-edit `uv.lock`, add deps only with `uv add`, mypy --strict for new Python,
security suppressions need a written reason on the same line.

All paths are relative to `/Users/adamtistler/PycharmProjects/k8s-fastapi-starter`.

Verification gates (run from repo root; RECORD what you ran and the result in the
commit message / a verification note so the reviewer reads the evidence without
re-running):
- `uv run poe check` — ruff lint + `mypy --strict` + pytest unit.
- `uv run poe k8s-check` — renders dev/prod/argocd, two kubeconform passes
  (pass (b) FAILS if any CNPG/Redis CRD schema is missing/mis-cased), kube-linter
  on dev/prod only.
- `uv run poe security` — gitleaks + ruff S + `scripts/check_sops_encrypted.py`.
- `uv run poe docs-build` — `mkdocs build --strict`.
- `rg PLACEHOLDER_` — lists only intended tokens (BACKUP_IMAGE + DB_PASSWORD
  gone; DB_STORAGE_SIZE/CLASS + BACKUP_PREFIX added).
- GitHub workflow YAML: actionlint is NOT installed locally — use
  `python -c "import yaml; yaml.safe_load(open('.github/workflows/sync-secrets.yml'))"`
  then `pre-commit run check-yaml --files .github/workflows/sync-secrets.yml`.

The order follows the design's §7 commit split; each item leaves the template
green at the gate named in its Verify line.

---

- [ ] 1. App config contract: Sentinel + CNPG-sourced DB, plain URLs for local dev.
      Rewrite `src/app/config.py` so `database_url` and `redis_url` become
      optional (`str | None`); add discrete `pg_host/pg_port: int | None /pg_user/
      pg_password/pg_dbname`, a `database_dsn` property (returns `database_url` when
      set, else composes `postgresql+asyncpg://{quote(user)}:{quote(pw)}@{host}:{port}/{dbname}`
      with BOTH user and password percent-quoted via `urllib.parse.quote(..., safe="")`),
      Sentinel fields `redis_sentinel_hosts: str | None`, `redis_master_name: str =
      Field(default="mymaster", min_length=1)`, `redis_password: str | None`, a
      `redis_is_sentinel` property (`bool(redis_sentinel_hosts)`), and a
      `model_validator(mode="after")` requiring one DB mode (DATABASE_URL or full
      PG_* set) AND one Redis mode (REDIS_SENTINEL_HOSTS or REDIS_URL) — "neither"
      is a fatal startup `ValueError`. Import `Field` from `pydantic`. Rewrite
      `src/app/cache.py`: add typed `parse_sentinel_hosts(raw: str) -> list[tuple[str, int]]`
      (comma-separated `host:port`, int port 1–65535, `ValueError` on malformed);
      change `create_redis(settings: Settings) -> Redis` to branch — Sentinel path
      `Sentinel(hosts, sentinel_kwargs={"password": ...}, password=..., decode_responses=True).master_for(redis_master_name)`,
      else `Redis.from_url(redis_url, decode_responses=True)`. Rewrite
      `src/app/db.py` `create_engine`: normalize leading `postgresql://` →
      `postgresql+asyncpg://`, then parse the query with `urlsplit`+`parse_qsl` and
      raise a `ValueError` naming the offender ONLY for the libpq-only keys
      `{sslmode,sslrootcert,sslcert,sslkey,sslpassword,gssencmode,sslnegotiation}`
      (case-insensitive) — NOT a blanket `?` ban; every other query param passes
      through. Update `src/app/main.py` lifespan: `create_engine(resolved.database_dsn)`
      and `create_redis(resolved)`. Update `migrations/env.py` `get_url()` to return
      `Settings().database_dsn` (keeps `-> str`). `health.py` and `items.py` need NO
      change (the master_for client's get/set/delete/ping are identical).
      Files: src/app/config.py, src/app/cache.py, src/app/db.py, src/app/main.py,
      migrations/env.py
      Verify: `uv run poe check` passes (ruff + mypy --strict + unit) — the Alembic
      env module typechecks and imports cleanly.

- [ ] 2. Unit tests + local-dev env for the new config contract.
      In `tests/unit/test_config.py`: REPLACE `test_missing_required_field_raises`
      (it encodes a now-false "each individually required" contract) with explicit
      mode-matrix cases — DB: DATABASE_URL alone ok; full PG_* alone ok
      (`database_dsn` composes); neither → `ValidationError`. Redis:
      REDIS_SENTINEL_HOSTS alone ok with `redis_is_sentinel is True`; REDIS_URL set
      → ok with `redis_is_sentinel is False`; neither → `ValidationError`. Add a
      `database_dsn` composition test (discrete PG_* → clean
      `postgresql+asyncpg://…` with NO query string; include a case with a
      URL-reserved char like `@`/`:` in user AND password to prove percent-quoting).
      Add a case asserting `REDIS_MASTER_NAME=""` raises `ValidationError`
      (`Field(min_length=1)`). Keep/adjust `test_required_fields_read_from_env` so
      it no longer asserts either field is *required* (may still assert they are
      read when present). Create `tests/unit/test_cache.py`: unit tests for
      `parse_sentinel_hosts` (valid, malformed, bad port) and `create_redis` mode
      selection using monkeypatch/mocks on `redis.asyncio.sentinel.Sentinel` and
      `Redis.from_url` (no network) — assert the Sentinel path constructs
      `Sentinel(...)` with parsed hosts, receives `decode_responses=True`, and
      calls `.master_for(master_name)`; assert plain path calls `Redis.from_url`.
      Create `tests/unit/test_db.py`: `create_engine` scheme normalization
      (`postgresql://`→`postgresql+asyncpg://`), already-`+asyncpg` untouched, a
      libpq `?sslmode=require` URL raises `ValueError` whose message NAMES
      `sslmode`, and a non-libpq query param (`?prepared_statement_cache_size=0`
      and bare `?ssl=disable`) is ACCEPTED unchanged (string-only, no DB). In
      `tests/conftest.py` keep `placeholder_env` setting plain DATABASE_URL +
      REDIS_URL (local mode) — add a comment why. In
      `tests/integration/test_items_api.py` add a docstring note: integration uses
      plain Redis; Sentinel is covered by the mocked unit test. Update `.env.example`:
      keep the existing DATABASE_URL + REDIS_URL local-dev lines; append the
      commented, explicitly mutually-exclusive cluster-only Sentinel block from
      design §D verbatim (REDIS_SENTINEL_HOSTS/REDIS_MASTER_NAME/REDIS_PASSWORD
      commented, warning that uncommenting switches to Sentinel and REDIS_URL must
      be removed; note in-cluster DATABASE_URL is composed from CNPG PG_* keys).
      Files: tests/unit/test_config.py, tests/unit/test_cache.py,
      tests/unit/test_db.py, tests/conftest.py, tests/integration/test_items_api.py,
      .env.example
      Verify: `uv run poe check` passes (new + replaced unit tests green with NO
      services). This completes design commit 1 (`feat(cache)!: …`).

- [ ] 3. Vendor the four CNPG + Redis CRD JSON schemas.
      Add `deploy/k8s/crd-schemas/Cluster_v1.json`, `ScheduledBackup_v1.json`,
      `Backup_v1.json` (CNPG `postgresql.cnpg.io/v1`) and `RedisFailover_v1.json`
      (`databases.spotahome.com/v1`), using CANONICAL kind casing (pinned
      kubeconform 0.8.0 has no `toLower`; the `-schema-location` template is
      `{{.ResourceKind}}_{{.ResourceAPIVersion}}.json`). Source order per design §4:
      primary = datreeio/CRDs-catalog raw URLs
      (`postgresql.cnpg.io/cluster_v1.json`, `scheduledbackup_v1.json`,
      `backup_v1.json`; `databases.spotahome.com/redisfailover_v1.json`); the
      Spotahome one is the at-risk entry — fallback is to fetch the operator CRD at
      a PINNED release ref (CloudNativePG 1.24+ release assets for CNPG;
      `spotahome/redis-operator` `manifests/databases.spotahome.com_redisfailovers.yaml`
      for Redis) and run `openapi2jsonschema` on `spec.versions[].schema.openAPIV3Schema`,
      then rename to canonical kind casing. Extend `deploy/k8s/crd-schemas/README.md`
      Files/apiVersions table with the four kinds and add PER-FILE provenance
      (catalog raw URL, or operator repo + pinned tag + `openapi2jsonschema`
      command); restate kind-collision safety (new kinds don't collide with
      Application/ExternalSecret/ClusterSecretStore across groups).
      Files: deploy/k8s/crd-schemas/Cluster_v1.json,
      deploy/k8s/crd-schemas/ScheduledBackup_v1.json,
      deploy/k8s/crd-schemas/Backup_v1.json,
      deploy/k8s/crd-schemas/RedisFailover_v1.json,
      deploy/k8s/crd-schemas/README.md
      Verify: `kubeconform` resolves each file by `kind+version` — proven at the
      end of item 5 by `uv run poe k8s-check` pass (b). Standalone now: the four
      JSON files parse (`python -c "import json; json.load(open(<f>))"`).

- [ ] 4. Add the CNPG Cluster + ScheduledBackup and the Redis Sentinel CRs.
      Create `deploy/k8s/base/postgres-cluster.yaml` — CNPG `Cluster`
      (`postgresql.cnpg.io/v1`) named `pg`: `instances: 3`, quorum sync
      `postgresql.synchronous: {method: any, number: 1}` (inline comment), preferred
      pod anti-affinity (`enablePodAntiAffinity: true`, `topologyKey:
      kubernetes.io/hostname`, `podAntiAffinityType: preferred`),
      `enableSuperuserAccess: false` (explicit, commented), `storage.size:
      PLACEHOLDER_DB_STORAGE_SIZE` + `storage.storageClass: PLACEHOLDER_DB_STORAGE_CLASS`,
      concrete `resources` requests/limits, `bootstrap.initdb` database `app`/owner
      `app` with `initdb.secret` DELIBERATELY OMITTED (so CNPG GENERATES the
      `pg-app` Secret), `serviceAccountTemplate.metadata.annotations["eks.amazonaws.com/role-arn"]:
      PLACEHOLDER_BACKUP_ROLE_ARN`, and `backup.barmanObjectStore` with
      `destinationPath: s3://PLACEHOLDER_BACKUP_BUCKET/PLACEHOLDER_BACKUP_PREFIX`,
      `s3Credentials.inheritFromIAMRole: true`, `wal/data compression: gzip`, plus
      `retentionPolicy: "30d"`. Create `deploy/k8s/base/scheduledbackup.yaml` —
      `ScheduledBackup`, `schedule: "0 0 3 * * *"` with an inline comment "CNPG
      6-field (seconds-first) cron, intentional — not 5-field", `cluster.name: pg`,
      `backupOwnerReference: self`. Create `deploy/k8s/base/redis-failover.yaml` —
      Spotahome `RedisFailover` (`databases.spotahome.com/v1`) named `redis`:
      `sentinel.replicas: 3`, `redis.replicas: 3` with resources/securityContext,
      `auth.secretPath: app-secrets` (operator reads the `password` key). Add
      `REDIS_SENTINEL_HOSTS: "rfs-redis:26379"` and `REDIS_MASTER_NAME: "mymaster"`
      to `deploy/k8s/base/configmap.yaml`. In `deploy/k8s/base/deployment.yaml` and
      `deploy/k8s/base/migration-job.yaml` project the CNPG `pg-app` discrete keys
      as env `PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DBNAME` via `secretKeyRef`
      (keys `host/port/username/password/dbname`; NOT `uri`), keeping the
      `app-secrets` `envFrom` (now carries `REDIS_PASSWORD`); add a comment in
      migration-job that migrations target `pg-rw` (the CNPG primary). Wire
      `deploy/k8s/base/kustomization.yaml`: add `postgres-cluster.yaml`,
      `scheduledbackup.yaml`, `redis-failover.yaml` (removal of the backup files is
      item 6). In `pyproject.toml` `[tool.poe.tasks.k8s-check]` add a comment that
      the CNPG + Redis kinds are now among the CRDs pass (b) must resolve, and DROP
      the now-dead `CronJob` entry from the pass-(b) `-skip` list.
      Before writing the `secretKeyRef` wiring, confirm the exact CNPG `pg-app`
      generated key names against the shipped 1.24+ line (design flags
      username/password/host/port/dbname as stable).
      Files: deploy/k8s/base/postgres-cluster.yaml,
      deploy/k8s/base/scheduledbackup.yaml, deploy/k8s/base/redis-failover.yaml,
      deploy/k8s/base/configmap.yaml, deploy/k8s/base/deployment.yaml,
      deploy/k8s/base/migration-job.yaml, deploy/k8s/base/kustomization.yaml,
      pyproject.toml
      Verify: deferred to item 5 (overlays still patch the deleted CronJob until
      item 6, but base renders). Interim: `kubectl kustomize deploy/k8s/base`
      renders the three CRs. (Completes design commit 2 together with items 3 & 5.)

- [ ] 5. Backup reconciliation: remove pg_dump CronJob, wire Barman per env, fix NetworkPolicy + kube-linter.
      Delete `deploy/k8s/base/backup-cronjob.yaml` and
      `deploy/k8s/base/backup-serviceaccount.yaml`; remove both from
      `deploy/k8s/base/kustomization.yaml` `resources:`. In both overlay
      kustomizations remove the `db-backup` `BACKUP_PREFIX` CronJob patch and ADD a
      MANDATORY patch setting the CNPG `Cluster` `spec.backup.barmanObjectStore.destinationPath`
      (and/or `serverName`) to a distinct per-env prefix (`dev` / `prod`). Rework
      `deploy/k8s/base/networkpolicy.yaml` egress rule 2: keep 5432/TCP + 6379/TCP,
      ADD 26379/TCP (Sentinel), keep 8126/TCP + 8125/UDP, REMOVE the 443/TCP entry
      AND all three backup-pod references (top-of-file egress description listing
      443 for S3/STS/KMS/Secrets Manager; the inline 443 comment; the TIGHTEN-example
      clause about the backup CronJob pod); rewrite the header + tighten-example to
      reference CNPG (`pg-rw`/`pg-ro`/`pg-r`) and Redis (`rfs-redis`/`rfr-redis`)
      Service labels. In `deploy/k8s/.kube-linter.yaml`: re-run kube-linter on the
      CronJob-deleted renders and reconcile the two exclusions whose reasons cite
      the deleted `backup-cronjob.yaml` — REMOVE `latest-tag` if it no longer fires
      (untagged image gone), and REWRITE the `job-ttl-seconds-after-finished` reason
      to cite the migration Job (still sets `ttlSecondsAfterFinished: 3600`), or
      remove it if it no longer fires. Update `scripts/bootstrap_backups.py` DOCSTRING
      only: object layout is now Barman (`<prefix>/base`, `<prefix>/wals`), not
      `db/<ts>.sql.gz`; retention still 30d; no behavioral change (its tests are
      unaffected).
      Files: deploy/k8s/base/backup-cronjob.yaml (delete),
      deploy/k8s/base/backup-serviceaccount.yaml (delete),
      deploy/k8s/base/kustomization.yaml, deploy/k8s/overlays/dev/kustomization.yaml,
      deploy/k8s/overlays/prod/kustomization.yaml,
      deploy/k8s/base/networkpolicy.yaml, deploy/k8s/.kube-linter.yaml,
      scripts/bootstrap_backups.py
      Verify: `uv run poe k8s-check` — both kubeconform passes resolve the four new
      CRD schemas (pass (b) would fail on any missing/mis-cased schema), dev/prod
      render the Cluster/ScheduledBackup/RedisFailover with per-env Barman path, and
      kube-linter is clean (any check that fires gets an exclusion with a written
      reason). `uv run poe check` still green (bootstrap tests unaffected — run them
      to confirm). (Completes design commits 2 & 3.)

- [ ] 6. Secrets/ESO reconciliation + placeholder token delta.
      Change both `deploy/k8s/overlays/{dev,prod}/externalsecret.yaml` from the
      single `dataFrom.extract` to an explicit two-entry `data:` block: both
      `secretKey: REDIS_PASSWORD` (app) and `secretKey: password` (Spotahome
      operator) map from the ONE SM property `REDIS_PASSWORD` under
      `remoteRef.key: <slug>/<env>/app-secrets` — guaranteeing identical strings.
      Change both `deploy/k8s/overlays/{dev,prod}/secrets.example.yaml` `stringData`
      to only `REDIS_PASSWORD: PLACEHOLDER_REDIS_PASSWORD` (drop the DATABASE_URL /
      PLACEHOLDER_DB_PASSWORD row). Edit `.github/workflows/sync-secrets.yml` at ALL
      SEVEN hardcoded `DATABASE_URL`/`REDIS_URL` sites per design §F: (1) header
      comment, (3) `expected='["REDIS_PASSWORD"]'`, (4) extra-key filter
      `select(. != "REDIS_PASSWORD")`, (5) missing-key loop `for k in REDIS_PASSWORD`,
      (6) both error messages, (7) success echo — leaving the `k8s-fastapi-starter`
      slug strings in SECRET_ID/header alone (separate PROJECT_SLUG concern). Update
      `.template/placeholders.yml`: REMOVE `PLACEHOLDER_BACKUP_IMAGE` and
      `PLACEHOLDER_DB_PASSWORD` fill entries; ADD `PLACEHOLDER_DB_STORAGE_SIZE`,
      `PLACEHOLDER_DB_STORAGE_CLASS`, `PLACEHOLDER_BACKUP_PREFIX` fill entries
      (files: `deploy/k8s/base/postgres-cluster.yaml`); update `files:` for
      `PLACEHOLDER_BACKUP_BUCKET`/`KMS_KEY_ID`/`ROLE_ARN` from `backup-cronjob.yaml`/
      `backup-serviceaccount.yaml` to `deploy/k8s/base/postgres-cluster.yaml`
      (+ `scripts/bootstrap_backups.py` where applicable); ADD `docs/configuration.md`
      to `PROJECT_SLUG.replace_in`; rewrite the NetworkPolicy label-coupling NOTE to
      the design §6 text (drop the deleted backup-pod clause; state CNPG/RedisFailover
      pods are operator-labeled, not slug-labeled). Confirm the Spotahome
      `auth.secretPath` key name is `password` before finalizing.
      Files: deploy/k8s/overlays/dev/externalsecret.yaml,
      deploy/k8s/overlays/prod/externalsecret.yaml,
      deploy/k8s/overlays/dev/secrets.example.yaml,
      deploy/k8s/overlays/prod/secrets.example.yaml,
      .github/workflows/sync-secrets.yml, .template/placeholders.yml
      Verify: `uv run poe k8s-check` green; `uv run poe security` green (example
      files hold only PLACEHOLDER_REDIS_PASSWORD; sops guard + gitleaks pass);
      `python -c "import yaml; yaml.safe_load(open('.github/workflows/sync-secrets.yml'))"`
      + `pre-commit run check-yaml --files .github/workflows/sync-secrets.yml` pass;
      `rg PLACEHOLDER_` lists only intended tokens (BACKUP_IMAGE + DB_PASSWORD gone;
      DB_STORAGE_SIZE/CLASS + BACKUP_PREFIX present). (Completes design commits 4 & 5.)

- [ ] 7. Docs + SPEC + ADR reversal.
      Edit `docs/adr/0001-technology-choices.md` IN PLACE (no new ADR): append a
      dated reversal note (managed-DB/cache non-goal reversed → in-cluster CNPG
      3-instance Cluster + quorum sync + Barman backups, and Redis Sentinel
      RedisFailover 3 sentinels / 1 master + 2 replicas; local dev stays Docker
      Compose) and add CNPG + Redis Sentinel to the Decision summary. Edit `SPEC.md`
      §1/§2 (flip the managed-DB/cache non-goal; add CNPG + Redis operator to the §2
      table as "assumed pre-installed, cluster-level"), §3 (add the three CRs + four
      vendored schemas; remove backup-cronjob.yaml + backup-serviceaccount.yaml from
      the tree; CNPG Barman replaces pg_dump CronJob), §5 config table (Sentinel
      vars + DATABASE_URL-vs-discrete-PG_* modes) AND the §5.1 stale sentence (Redis
      password in SOPS as `REDIS_PASSWORD`; Postgres credential from CNPG `pg-app`,
      not SOPS; keep the DD-config-is-non-secret point), §7 (migrations run against
      `pg-rw`; first-boot ordering caveat; `migrations/env.py` reads `database_dsn`),
      §9 (reconciled secret flow: REDIS_PASSWORD → two keys; DB no longer a SOPS key),
      §10 (CNPG ScheduledBackup replaces the pg_dump CronJob narrative), §13 (mark
      managed-DB/cache Decided → ADR 0001). Update `docs/deployment.md` (CNPG + Redis
      operators assumed pre-installed, CNPG 1.24+ floor; Barman backups replace the
      CronJob; first-boot ordering rough edge next to the ESO first-sync note),
      `docs/architecture.md` (Kubernetes shape: CNPG Cluster 3 instances + Redis
      Sentinel replace external URL endpoints), `docs/secrets.md` (`app-secrets` key
      set now `{REDIS_PASSWORD}`; DATABASE_URL from CNPG's generated Secret),
      `docs/configuration.md` (Redis Sentinel vars; DATABASE_URL vs discrete PG_*
      source; add the slug-bearing `<slug>/<env>/app-secrets` Secrets Manager id
      example now covered by PROJECT_SLUG.replace_in).
      Files: docs/adr/0001-technology-choices.md, SPEC.md, docs/deployment.md,
      docs/architecture.md, docs/secrets.md, docs/configuration.md
      Verify: `uv run poe docs-build` (`mkdocs build --strict`) passes. (Completes
      design commit 6.)

- [ ] 8. Full-suite integration verification + evidence note.
      Run the complete gate from repo root and fix any cross-cutting seams:
      `uv run poe check`, `uv run poe k8s-check`, `uv run poe security`,
      `uv run poe docs-build`, `rg PLACEHOLDER_`, and the sync-secrets YAML
      validation. Record the exact commands run and their pass/fail results in a
      verification note (and in the commit messages) so the reviewer can read the
      evidence without re-running the suites.
      Files: (verification note under the task dir; no source change expected)
      Verify: every gate above green; `rg PLACEHOLDER_` lists only intended tokens.

---

## Assumptions / notes for the implementer

- CNPG and the Redis operator are assumed pre-installed cluster-side (same stance
  as ESO / the Datadog Agent); this repo ships only the CRs, never the operators.
- Operator-dependent facts the design flags as "verify at implementation, do not
  assume": CNPG `pg-app` generated key names (username/password/host/port/dbname),
  that omitting `bootstrap.initdb.secret` makes CNPG generate `pg-app`, the
  Spotahome `auth.secretPath` key name `password`, `barmanObjectStore`
  `inheritFromIAMRole` + bucket-default SSE-KMS behavior, `enableSuperuserAccess:
  false`, kube-linter emitting zero new diagnostics on the CRs, datreeio catalog
  availability of the four schemas (CRD-derived fallback in item 3), and the CNPG
  6-field cron. Confirm each at the point it is used; none changes a decision.
- `health.py` and `items.py` are intentionally untouched — the Sentinel
  `master_for` client is API-identical for get/set/delete/ping.
- actionlint is not installed locally; use the YAML-parse + `pre-commit check-yaml`
  fallback for `sync-secrets.yml`.
