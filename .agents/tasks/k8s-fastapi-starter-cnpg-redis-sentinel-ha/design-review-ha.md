# Design Review (round 4) — In-cluster HA Postgres (CNPG) + Redis Sentinel

Scope: `design-ha.md` for the in-cluster CloudNativePG + Redis Sentinel HA
change. Reviewed fresh against the repo, `SPEC.md` constraints, and the prior
batch context (`.../k8s-fastapi-starter-feat-argocd-eso/context.json`). Settled
decisions (CNPG, Redis Sentinel, SPEC §2 reversal, pg_dump CronJob removal) were
NOT re-litigated. The seven focus areas from the step prompt were each checked
against source.

This design has already absorbed three prior review rounds; its §9 responses are
largely accurate and were spot-checked against the repo. The remaining findings
below are what a cold read still surfaces.

---

## Findings

### 1. MEDIUM — `pyproject.toml` `k8s-check` pass-(a) and pass-(b) comments will be left factually wrong ("the three vendored CRDs")

The design's Modify row for `pyproject.toml` says only: *"comment that CNPG +
Redis CRDs are now pass-(b)-resolved; drop the now-dead `CronJob` entry from the
pass-(b) `-skip` list."* It does NOT say to correct the two existing comments
that hardcode the count of vendored CRDs. Verified in `pyproject.toml`:

- Pass (a) comment: *"core + the three vendored CRD schemas resolve here too."*
- Pass (b) comment: *"the only kinds pass (b) must resolve are the three
  vendored CRDs (Application, ExternalSecret, ClusterSecretStore)."*

After this batch there are **seven** vendored schemas (adds `Cluster`,
`ScheduledBackup`, `Backup`, `RedisFailover`), and pass (b) must resolve
`Application`, `ExternalSecret`, `ClusterSecretStore`, `Cluster`,
`ScheduledBackup`, `RedisFailover` (six rendered kinds; `Backup` is vendored but
not rendered). Leaving the "three vendored CRDs / (Application, ExternalSecret,
ClusterSecretStore)" wording is exactly the stale-reference class the design
itself fixes for the NetworkPolicy comments (finding 8), the `.kube-linter.yaml`
reasons (finding 2), and the `placeholders.yml` NOTE (finding 7). It is
inconsistent to fix those three and leave this one.

CONCRETE FIX: in the `pyproject.toml` Modify row, require rewriting **both**
comments, e.g. pass (b): *"the kinds pass (b) must resolve are the vendored CRDs
rendered by the overlays/argocd: `Application`, `ExternalSecret`,
`ClusterSecretStore`, `Cluster`, `ScheduledBackup`, `RedisFailover` (the
`Backup` schema is vendored for ad-hoc restores but is not rendered). A
missing/mis-named/version-drifted schema fails the build every run."* Rewrite the
pass (a) "three vendored CRD schemas" phrase to "the vendored CRD schemas"
(count-free) so it does not go stale again.

### 2. MEDIUM — Dropping `CronJob` from the pass-(b) `-skip` list is under-verified against kustomize behavior, and the design offers two mutually exclusive instructions

The design (round-3 finding 3 response + Modify row) instructs: *"drop the
now-dead `CronJob` entry from the pass-(b) `-skip` list"* because no `CronJob` is
rendered after the backup CronJob is deleted. The verified `-skip` list is:
`Namespace,ServiceAccount,ConfigMap,Secret,Service,Deployment,Job,CronJob,NetworkPolicy,PodDisruptionBudget,HorizontalPodAutoscaler`.

Two problems:

- The claim "no `CronJob` is rendered" is correct **only if** the three new
  operator CRs (`Cluster`, `RedisFailover`, `ScheduledBackup`) do not themselves
  render a `CronJob`. They do not — they are CRs, the operators create workloads
  at runtime, not at kustomize-render time — so the claim holds. But the design
  states it as settled without this one-line reasoning, and an implementer who
  drops `CronJob` must still re-run pass (b) to confirm no core `CronJob`
  surfaces. Not a blocker on its own.
- The design gives **two contradictory instructions in the same paragraph**:
  "Chosen: drop `CronJob`" and then "If an implementer prefers to retain it
  defensively … either is acceptable." For a design that is supposed to remove
  ambiguity before coding, "either is acceptable" on a concrete edit is an
  ambiguity. Pick one.

CONCRETE FIX: make it a single instruction — *"Remove `CronJob` from the pass-(b)
`-skip` list; the backup CronJob is the only CronJob the repo rendered and it is
deleted this batch. Do not add it back."* Drop the "either is acceptable"
alternative (or, if retention is actually wanted for consumer-added CronJobs,
say "keep `CronJob`" and drop the "chosen: drop it" sentence). One decision, not
two.

### 3. NIT — `ScheduledBackup` schema resolution in pass (b) depends on `Backup`/`ScheduledBackup` being *rendered*, and the vendored `Backup_v1.json` is explicitly never rendered

`§4` vendors `Backup_v1.json` "for completeness … not shipped as a manifest."
That is fine for pass (b) (pass (b) only fails on *rendered* kinds that cannot
resolve; an unused schema file is inert). But the "Create" table lists
`Backup_v1.json` as a hard deliverable while `scheduledbackup.yaml` is rendered
and `backup` is not — so only `Cluster`, `ScheduledBackup`, and `RedisFailover`
are actually gated by pass (b). The design is internally consistent here, but
the `crd-schemas/README.md` provenance note should state plainly that
`Backup_v1.json` is vendored-but-unrendered so a later reader does not assume a
missing `Backup` manifest is a bug.

CONCRETE FIX: in the `crd-schemas/README.md` Modify row, add: *"mark
`Backup_v1.json` as vendored for ad-hoc restore use and NOT rendered by any
overlay (so it is not exercised by the k8s-check pass-(b) gate)."*

### 4. NIT — `migrations/env.py` docstring will contradict the new `get_url()` body

Verified: `migrations/env.py` opens with the module docstring *"Alembic
environment: async engine, URL from app.config.Settings."* and `get_url()`
currently returns `Settings().database_url`. The design correctly changes
`get_url()` to return `Settings().database_dsn` (finding 1, commit 1) but does
not mention the docstring. "URL from app.config.Settings" stays broadly true, so
this is only a NIT, but the precise line ("from app.config.Settings") would read
more accurately as "composed DSN from app.config.Settings (DATABASE_URL locally,
discrete PG_* in-cluster)."

CONCRETE FIX: when editing `get_url()`, update the one-line module docstring to
name `database_dsn` as the source, mirroring the §F/main.py wording.

---

## Verified Assumptions

These design claims were checked against the actual repo/source and are
**correct**:

1. `src/app/config.py` — `Settings` has **required** `database_url: str` and
   `redis_url: str`, optional `app_env`/`log_level`, `env_file=".env"`. Confirmed.
2. `src/app/cache.py` — `create_redis(redis_url: str)` via
   `Redis.from_url(redis_url, decode_responses=True)`; `get_redis` reads
   `request.app.state.redis`; `RedisDep` alias. Confirmed. Changing the signature
   to `create_redis(settings)` touches one call site in `main.py`. Confirmed.
3. `src/app/main.py` lifespan — `create_engine(resolved.database_url)` and
   `create_redis(resolved.redis_url)`, `await redis.aclose()` then
   `engine.dispose()`. Confirmed. The two edits are exactly as the design states.
4. `migrations/env.py` — `get_url() -> str` returns `Settings().database_url`;
   both offline (`context.configure(url=get_url())`) and async-online
   (`create_async_engine(get_url(), …)`) call it. Confirmed. The design's
   finding-1 HIGH (it breaks at runtime under optional `database_url` AND fails
   `mypy --strict` on `-> str` vs `str | None`) is a **real, correctly-diagnosed
   bug**, and the one-line fix (`return Settings().database_dsn`, keeps `-> str`
   because `database_dsn` is `-> str`) is correct. Placing it in commit 1 is
   right.
5. `src/app/db.py` — `create_engine(database_url: str)` currently does no scheme
   normalization or query-param guarding. The design's added guard is a net-new
   behavior, and the "parameter-specific, not blanket `?` ban" shape (round-3
   finding 1) is sensible and will not regress a valid local `?ssl=disable` DSN.
6. `src/app/api/health.py` `_check_redis` calls `request.app.state.redis.ping()`;
   `items.py` uses `redis.get/set/delete`. The `master_for(...)` client exposes
   the identical interface, so the "no change to health.py/items.py" claim holds.
7. `.github/workflows/sync-secrets.yml` — the `{DATABASE_URL, REDIS_URL}`
   key-set assertion appears at exactly the enumerated sites: header comment,
   `expected='["DATABASE_URL","REDIS_URL"]'`, the extra-key `select(...)` filter,
   the `for k in DATABASE_URL REDIS_URL` loop, the "only … are allowed" error,
   the "key set must be exactly {…}" error, and the "key shape OK (…)" echo. The
   design's seven-site enumeration (finding 3) is accurate and complete. The
   IAM/secret-id commentary carries no key names (correctly "leave as is").
8. The `k8s-fastapi-starter` slug in `sync-secrets.yml` `SECRET_ID`/header is a
   separate `PROJECT_SLUG` concern already covered by `PROJECT_SLUG.replace_in`
   (the `.github/workflows/sync-secrets.yml` entry exists). The design's scope
   note (leave slug alone in key-set edits) is correct.
9. `deploy/k8s/base/backup-cronjob.yaml` uses a 5-field `schedule: "0 3 * * *"`;
   the design's contrast with CNPG's 6-field (seconds-leading) `ScheduledBackup`
   cron and the required inline comment are warranted.
10. `deploy/k8s/base/networkpolicy.yaml` — egress rule 1 is DNS to kube-system;
    egress rule 2 lists 5432/6379/8126(TCP)/8125(UDP)/443(TCP); the backup-pod
    443 justification appears in **three** places (top-of-file egress
    description, the inline 443-port comment, and the commented TIGHTEN example).
    The design's finding-8 "all three must go" is accurate.
11. `deploy/k8s/.kube-linter.yaml` — excludes exactly two checks (`latest-tag`,
    `job-ttl-seconds-after-finished`) and both written reasons cite the backup
    CronJob. `migration-job.yaml` **does** set `ttlSecondsAfterFinished: 3600`,
    so `job-ttl` very likely still fires (keep, rewrite reason) and `latest-tag`
    likely goes dead (only `:0.0.0`-tagged images remain after deletion). The
    design's MEDIUM finding-2 plan is correct and correctly scoped to commit 3.
12. `deploy/k8s/overlays/{dev,prod}/kustomization.yaml` — both carry a
    **mandatory** `db-backup` `BACKUP_PREFIX` patch (comment says "Mandatory").
    The design's requirement that the replacement CNPG `destinationPath`/
    `serverName` per-env patch is likewise **mandatory** (finding 11) correctly
    mirrors the existing contract.
13. `deploy/k8s/overlays/*/externalsecret.yaml` — currently a single
    `dataFrom[0].extract.key: <slug>/<env>/app-secrets`. The design's change to
    an explicit two-key `data:` block (`REDIS_PASSWORD` + `password`, both from
    the one SM property) is a real structural change, correctly described.
14. `deploy/k8s/overlays/*/secrets.example.yaml` — `stringData` carries
    `DATABASE_URL` (`PLACEHOLDER_DB_PASSWORD`) + `REDIS_URL`
    (`PLACEHOLDER_REDIS_PASSWORD`). Dropping the DB row and removing
    `PLACEHOLDER_DB_PASSWORD` is consistent with CNPG owning the DB credential.
15. `.template/placeholders.yml` — `PLACEHOLDER_BACKUP_IMAGE` and
    `PLACEHOLDER_DB_PASSWORD` exist as `fill` entries; the NetworkPolicy
    label-coupling NOTE references the backup CronJob pod (in both the NOTE body
    AND the `replace_in` inline comment). `PROJECT_SLUG.replace_in` does NOT list
    `docs/configuration.md`. The design's token delta (remove IMAGE + DB_PASSWORD;
    add DB_STORAGE_SIZE/CLASS + BACKUP_PREFIX; re-point BUCKET/KMS/ROLE_ARN
    `files:`) and the §6 NOTE rewrite are all consistent with the file.
16. `docs/configuration.md` exists and contains **no** `k8s-fastapi-starter`
    occurrence today. The design adds a slug-bearing `<slug>/<env>/app-secrets`
    example to it this batch and simultaneously adds it to
    `PROJECT_SLUG.replace_in` — internally consistent (the slug exists only after
    the batch writes it). Correct, not a gap.
17. `.env.example` — current `DATABASE_URL`/`REDIS_URL` lines carry
    `local-only-password` and exactly match the design's proposed verbatim block
    leading lines; `docker-compose.yml` runs plain `postgres:17` + `redis:7
    --requirepass local-only-password`. The "local dev unchanged + plain path"
    promise holds: Compose reads `.env` (`REDIS_URL`), never the cluster
    ConfigMap, so no pod reaches the Sentinel branch locally.
18. `tests/conftest.py` `placeholder_env` sets `DATABASE_URL` + `REDIS_URL`;
    `tests/unit/test_config.py` asserts both are read and has the parametrized
    `test_missing_required_field_raises(["DATABASE_URL","REDIS_URL"])`. The
    design's finding-4 "replace the parametrized test; neither field is
    individually required after the change" is accurate — with one removed, the
    *other mode's* validator would fire and the old test would pass for the wrong
    reason.
19. `pyproject.toml` `[tool.poe.tasks.k8s-check]` — two kubeconform passes,
    pass (b) with `-ignore-missing-schemas` removed and `-schema-location` only
    the vendored dir, core kinds `-skip`ped (including `CronJob`), kube-linter on
    dev/prod only. The `-schema-location` template
    `{{.ResourceKind}}_{{.ResourceAPIVersion}}.json` omits the API group. The
    design's two-pass-gate reasoning (new CRDs auto-resolve in pass (a), MUST
    resolve in pass (b), no `-skip` change needed for the new CRD kinds) is
    correct.
20. `deploy/k8s/base/migration-job.yaml` / `deployment.yaml` — both consume
    `app-secrets` via `secretRef`/`envFrom`. Adding discrete `PG_*`
    `secretKeyRef` env from `pg-app` while keeping the `app-secrets` `envFrom`
    (now carrying `REDIS_PASSWORD`) is coherent with both manifests.

---

## Unverified / Wrong Assumptions

These are claims the design makes that could NOT be verified from the repo (they
depend on external operators / CRDs). The design **already flags every one of
these** as "verify at implementation, do not assume," with stated mitigations —
so they are not re-raised as blocking findings. They are listed here for the
implementer's checklist:

1. **CNPG generates a Secret named `pg-app` with keys
   `username/password/host/port/dbname` (+`uri`/`jdbc-uri`/`pgpass`) when
   `spec.bootstrap.initdb.secret` is omitted.** Not verifiable from this repo (no
   CNPG operator present). The design depends only on the five discrete keys and
   says to confirm the key set against the shipped CNPG 1.24+ line. Load-bearing:
   if the generated Secret name or the `host`/`port`/`dbname`/`username`/
   `password` key names differ, every `secretKeyRef` in the Deployment/migration
   Job is wrong. MUST be verified before writing the manifest env block.
2. **Spotahome `RedisFailover` `spec.auth.secretPath` reads a key literally named
   `password`.** Not verifiable here. The entire no-drift two-key ESO scheme
   (`REDIS_PASSWORD` for the app, `password` for the operator, both from one SM
   property) hinges on this exact key name. MUST be verified against the pinned
   operator release.
3. **`barmanObjectStore` with `s3Credentials.inheritFromIAMRole: true` picks up
   the IRSA role from `spec.serviceAccountTemplate`, and the bucket-default
   SSE-KMS (set by `bootstrap_backups.py`) is sufficient with no per-object
   `--sse-kms-key-id`.** Unverifiable here; flagged by the design. If CNPG needs
   an explicit KMS key id on the object store, `PLACEHOLDER_BACKUP_KMS_KEY_ID`
   must be wired into the `Cluster` rather than only documented.
4. **The datreeio/CRDs-catalog publishes all four schemas at the stated raw URLs,
   especially `databases.spotahome.com/redisfailover_v1.json`.** Unverifiable
   offline. The design provides a concrete `openapi2jsonschema`-from-CRD fallback
   at a pinned ref, so the implementer is not blocked — adequate mitigation.
5. **`spec.enableSuperuserAccess: false` renders as expected on the 1.24+ line
   and the generated app role has DDL privilege sufficient for Alembic
   migrations.** Flagged; set explicitly in the CR. Must confirm the app role can
   run the migrations (CREATE TABLE etc.) since migrations run as the app role,
   not a superuser.
6. **kube-linter emits zero new diagnostics against the `Cluster` /
   `RedisFailover` / `ScheduledBackup` CRs.** Likely (kube-linter targets
   workload kinds, not unknown CRDs) but explicitly "verify on the render," with
   an exclusion-with-reason fallback. Adequate.
7. **CNPG `spec.schedule` is a 6-field (seconds-leading) robfig/cron expression.**
   Flagged; the required inline comment mitigates the "implementer normalizes to
   5 fields" risk. Confirm against the CNPG `ScheduledBackup` schema.
8. **`decode_responses=True` passed to `Sentinel(...)` propagates to the
   `master_for(...)` client's connection pool.** Flagged, with a dedicated
   `test_cache.py` assertion pinning it. Adequate mitigation.

None of the above changes a design decision; each has a stated verification step
and/or fallback.

---

## Verdict rationale

HIGH findings: 0. MEDIUM findings: 2 (findings 1 and 2). NIT findings: 2
(findings 3 and 4). Per the mechanical rule (any HIGH or MEDIUM ⇒
CHANGES_REQUESTED), the verdict is **CHANGES_REQUESTED**. Both MEDIUMs are
small, localized `pyproject.toml` wording/decision fixes — a quick loop-back,
not a structural rework. The core architecture (CNPG credential sourcing via
discrete `pg-app` keys, the Sentinel two-mode config, the ESO two-key
materialization, the backup reconciliation, and the two-pass CRD gate) is sound
and the three prior rounds' findings are genuinely resolved.
