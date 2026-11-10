# Design: Argo CD GitOps + SOPS → AWS Secrets Manager → External Secrets Operator

## Overview

This change reverses the template's earlier "deployment is a CI apply job, not
GitOps" decision and adopts a GitOps delivery model built on four pieces that
chain together:

1. **SOPS stays the git source of truth.** `deploy/k8s/overlays/{dev,prod}/secrets.{dev,prod}.yaml`
   remain SOPS-encrypted, committed, and decryptable locally by anyone with KMS
   access — exactly as today.
2. **A new CI job (`sync-secrets`), in its own workflow file,** decrypts the
   changed `secrets.<env>.yaml` and writes it to **AWS Secrets Manager** as one
   JSON secret per environment, using the existing GitHub OIDC-to-AWS role.
3. **An `ExternalSecret` CR per overlay** (a pointer with zero secret material)
   tells the **External Secrets Operator (ESO)** to materialize the `app-secrets`
   Kubernetes Secret from the Secrets Manager entry.
4. **Argo CD `Application` manifests (dev + prod)** apply all manifests as plain
   kustomize output. No KSOPS plugin, no in-cluster decrypt hook. The migration
   Job's "migrate before the Deployment rolls out" ordering is expressed as an
   Argo CD PreSync hook (wave `-1`).

The net effect: rotating a secret is `sops <file>` → commit. CI pushes it to
Secrets Manager; ESO syncs it into the cluster; Argo CD never touches ciphertext.
Two IAM roles (CI write-only, ESO read-only) provide the isolation, so the
**default Secrets Manager key is intentionally fine** — no customer-managed key
(CMK) for secrets. A CMK is provisioned **only for the S3 backup bucket**.

This design is for the template itself. All identity/ARN values stay as
`PLACEHOLDER_<NAME>` tokens so the template builds and passes CI before
initialization. ESO and Argo CD are **assumed pre-installed cluster-level**,
exactly like the Datadog Agent DaemonSet — this repo does **not** install either.

### Locked technology stack (do not change during implementation)

- GitOps controller: **Argo CD** (`argoproj.io/v1alpha1`, `kind: Application`).
- Secrets runtime: **SOPS (git) → AWS Secrets Manager → External Secrets
  Operator** (`external-secrets.io/v1`, `kind: ExternalSecret` +
  `ClusterSecretStore`). **Minimum ESO version: v0.10.0** — ESO only promoted
  its API group from `v1beta1` to `v1` in v0.10.0, so a cluster running an older
  ESO serves only `v1beta1` and would reject these manifests
  (`no matches for kind "ExternalSecret" in version "external-secrets.io/v1"`).
  This floor is **locked**: the manifest `apiVersion` (`external-secrets.io/v1`),
  the vendored CRD schema filenames (`externalsecret_v1.json`,
  `clustersecretstore_v1.json`), and the documented v0.10.0 floor in
  `docs/deployment.md` must all agree. Bumping below `v1` is a stack change, not
  an implementation detail (review finding 1).
- Secrets Manager entry shape: **one JSON secret per environment** named
  `k8s-fastapi-starter/<env>/app-secrets`, keys `DATABASE_URL` and `REDIS_URL`.
- Backup: **S3 bucket + KMS CMK for backups only**, daily **CronJob** running
  `pg_dump` to S3.
- Bootstrap tooling: a **Python script** under `scripts/` (stdlib + `boto3`
  via `uv run`), matching `scripts/check_sops_encrypted.py` conventions.
- CRD validation: **vendored CRD JSON schemas** under `deploy/k8s/crd-schemas/`,
  fed to kubeconform via `-schema-location` (hermetic, no network dependency).
- Everything else (kustomize, kubeconform, kube-linter, poe, GitHub Actions,
  OIDC) is unchanged.

---

## Current state (what exists today — verified against the repo)

- `deploy/k8s/base/` — `serviceaccount.yaml`, `configmap.yaml`, `deployment.yaml`,
  `service.yaml`, `networkpolicy.yaml`, `migration-job.yaml`, `kustomization.yaml`
  (resources listed in that order).
- `deploy/k8s/overlays/{dev,prod}/kustomization.yaml` — set namespace
  (`fastapi-starter-dev` / `fastapi-starter-prod`), image, replicas, patches.
  Each has a comment block (BEFORE DEPLOY … and "secrets.<env>.yaml is
  intentionally NOT listed under `resources`") describing the manual
  decrypt-and-apply flow. `secrets.example.yaml` holds placeholder `app-secrets`
  content.
- `deployment.yaml` and `migration-job.yaml` both consume `envFrom: - secretRef:
  name: app-secrets`. **Confirmed: the consumed Secret name is already
  `app-secrets` in both manifests and in `secrets.example.yaml`. No rename is
  required.**
- The migration Job (`metadata.name: app-migrate`, overlay-renamed to
  `app-migrate-{dev,prod}`) has **no Argo CD annotations today** — ordering is
  enforced imperatively by `.github/workflows/deploy.yml` (SHA-append + wait +
  exclude-base-named-Job). It sets `serviceAccountName: app`,
  `automountServiceAccountToken: false`, runs non-root (uid 10001), read-only
  rootfs, dropped caps, with a `tmp` emptyDir.
- `deploy/k8s/base/serviceaccount.yaml` — SA `app`,
  `automountServiceAccountToken: false`, **no IRSA annotation** (the app never
  calls AWS).
- `deploy/k8s/base/networkpolicy.yaml` — `NetworkPolicy` named `app`, selects
  `app.kubernetes.io/name: k8s-fastapi-starter`. Egress has **two** rules: (1) a
  `kube-system`-scoped DNS rule (53/UDP, 53/TCP), and (2) a **second rule with no
  `to:` (any-IP)** opening 5432/TCP, 6379/TCP, 8126/TCP, 8125/UDP.
- `.github/workflows/ci.yml` — workflow-level `on: { pull_request:, push:
  { branches: [main] } }` with **no `paths:`**. Jobs: `lint`, `commits`, `test`,
  `audit`, `k8s`, `docs`, `image`. Actions pinned to full SHAs with a version
  comment. The `k8s` job installs kubeconform `0.8.0` / kube-linter `0.8.3` and
  calls `uv run poe k8s-check`.
- `.github/workflows/deploy.yml` — manual `workflow_dispatch`; installs sops,
  assumes `vars.AWS_DEPLOY_ROLE_ARN`, then a **deliberately-failing kubeconfig
  TODO** (`SPEC §13 item 3`); the remaining steps decrypt SOPS, apply the Secret,
  run+wait the migration Job (SHA-appended name), then apply the Deployment.
- `pyproject.toml` `k8s-check` task: `for env in dev prod; do kubectl kustomize
  deploy/k8s/overlays/$env > "$tmp/$env.yaml"; done`, then
  `kubeconform -strict -summary -ignore-missing-schemas "$tmp/dev.yaml"
  "$tmp/prod.yaml"` and `kube-linter lint "$tmp/dev.yaml" "$tmp/prod.yaml"`.
  `-ignore-missing-schemas` means unknown CRDs are **skipped silently** today.
- `deploy/k8s/.kube-linter.yaml` — default check set, empty `exclude: []`.
- `scripts/check_sops_encrypted.py` — guards `deploy/**/secrets.*.yaml`
  (`secrets.example.*` excluded), stdlib only, typed, `main(argv) -> int`.
- `.template/placeholders.yml` — fill/rename/keep/runtime/external registry.
  `PROJECT_SLUG.replace_in` already globs `deploy/k8s/**` but **not**
  `deploy/argocd/**` or `.github/workflows/**`.
- SPEC.md §1 lists GitOps as a non-goal; §13 Q3 (deploy target / GitOps-vs-CI)
  and Q4 (SOPS-vs-ESO) are open; Q9 (observability) is the model for a "Decided —"
  entry. ADR 0001 is "Accepted" and has the "Deployment is a CI job, not GitOps"
  Consequences bullet.

---

## Architecture decisions

### Why Argo CD `Application` (app-of-apps not used)

Two standalone `Application` manifests (dev, prod), each pointing at its overlay
path, are simpler than an app-of-apps for a single-service template and keep the
template honest: a consumer registers each `Application` in whichever Argo CD
instance their platform runs. An app-of-apps would imply this repo owns an Argo
CD project/parent app, which over-reaches a starter template. **Chosen: two
plain `Application` CRs** in `deploy/argocd/`.

### Why ordering is a PreSync hook, not a sync-wave alone

Argo CD needs the migration Job to run to completion *before* the Deployment
rolls out. Two mechanisms exist: resource **hooks** (`PreSync`) and **sync
waves** (`argocd.argoproj.io/sync-wave`). A `PreSync` hook runs and must succeed
before the main sync; a sync-wave only orders resources within the main sync and
does not gate on Job *completion* the way a hook does. The task requires
"migrate-before-Deployment ordering as a PreSync hook," so the migration Job
gets `argocd.argoproj.io/hook: PreSync` plus `hook-delete-policy:
BeforeHookCreation` (so each sync recreates the immutable Job) and
`sync-wave: "-1"`. **Chosen: PreSync hook on the migration Job.** This replaces
the imperative ordering that `deploy.yml` did; the ordering intent is preserved,
now declaratively.

Because the Job becomes an Argo CD hook, Argo CD manages its lifecycle.
`hook-delete-policy: BeforeHookCreation` deletes the prior hook Job before
creating the next, so the per-run unique-naming dance that `deploy.yml` performed
(append commit SHA) is **no longer needed** under GitOps — it sidesteps the
immutable-template collision. The overlays still rename the base Job to
`app-migrate-{dev,prod}` (that is per-environment identity, unrelated to the SHA
dance). The base Job keeps `ttlSecondsAfterFinished` as a backstop. **The
migration-job header comment must be rewritten** (see Files to modify) so it
describes the PreSync-hook lifecycle instead of the obsolete imperative flow.

### Why one JSON secret per environment

Secrets Manager bills per secret and per 10k API calls. One JSON entry
(`{"DATABASE_URL": "...", "REDIS_URL": "..."}`) per environment is cheaper and
maps cleanly to the single `app-secrets` Kubernetes Secret with two keys. ESO's
`ExternalSecret` extracts both keys from the one Secrets Manager entry via
`dataFrom.extract`. **Chosen: one JSON secret per env**, named
`k8s-fastapi-starter/<env>/app-secrets`.

### Why ESO reads via `ClusterSecretStore` + IRSA, and where the store lives

ESO authenticates to Secrets Manager with its **own read-only IRSA role**
(assumed configured on the ESO controller's ServiceAccount at the cluster level,
like the Datadog Agent). The repo ships **one** `ClusterSecretStore` referencing
the AWS provider and region; the per-overlay `ExternalSecret` references it by
name. A `ClusterSecretStore` (cluster-scoped) is chosen over a namespaced
`SecretStore` so one store serves both namespaces and matches the "assumed
pre-installed, cluster-level" posture. The store holds **no credentials** — it
names the region and relies on the controller's IRSA, so it is safe to commit.

**The `ClusterSecretStore` must NOT go in `deploy/k8s/base/`.** Both overlays set
`namespace:`, and kustomize's namespace transformer stamps `metadata.namespace`
on every resource it does not know to be cluster-scoped. `ClusterSecretStore` is
a CRD kustomize has no built-in knowledge of, so a base-placed store would render
with a (invalid, confusing) namespace in each overlay, producing two divergent
cluster-scoped objects — defeating the "one store" rationale (review finding 3).

**Chosen placement:** the `ClusterSecretStore` lives in the new
`deploy/argocd/` kustomization (alongside the Application manifests), which has
**no `namespace:` transformer**, so it renders cluster-scoped and un-namespaced.
It is a cluster-scoped prerequisite a platform applies once, exactly like the
Argo CD `Application` CRs themselves — grouping both cluster-scoped, "register
once per cluster" resources in `deploy/argocd/` is coherent. The per-overlay
`ExternalSecret` (namespaced, correctly stamped by the overlay transformer)
references the store by name `aws-secrets-manager`. The verification gate must
confirm `kubectl kustomize deploy/argocd` emits the `ClusterSecretStore` with
**no `metadata.namespace`**.

### Why the default Secrets Manager key (no secrets CMK)

The isolation a CMK key policy would give is already provided by two distinct
IAM roles: the CI OIDC role has `secretsmanager:PutSecretValue`/`CreateSecret`
scoped to the app's secret ARNs (write-only), and ESO's IRSA role has
`secretsmanager:GetSecretValue`/`DescribeSecret` (read-only). The AWS-managed
default key is therefore sufficient for Secrets Manager. The **only** CMK this
design provisions is for the **S3 backup bucket** (encrypting database dumps at
rest). This is an explicit, documented split.

### Disposition of `deploy.yml` (the superseded manual apply)

`deploy.yml` is the manual `workflow_dispatch` apply job that GitOps now
supersedes, and it carries the deliberately-failing kubeconfig TODO tied to
SPEC §13 item 3. Once Q3 is marked **Decided**, leaving `deploy.yml` untouched
would ship a second, contradictory deployment path and a dangling TODO (review
finding 8). **Chosen: remove `.github/workflows/deploy.yml` entirely.** GitOps
(Argo CD) is the delivery path; break-glass manual apply is documented in
`docs/deployment.md` as an operator running `kubectl apply -k` against a cluster
they have direct access to — it needs no committed workflow. The
`AWS_DEPLOY_ROLE_ARN` / `AWS_REGION` GitHub Environment variables are **kept**
(the new `sync-secrets` workflow consumes them); update their `referenced_in`
in `placeholders.yml` from `deploy.yml` to the new workflow.

### ADR process reconciliation (editing ADR 0001 in place)

The user's instruction is explicit: "just update existing ADRs, you don't have to
create a new one." But ADR 0001 today carries a Consequences bullet that
**forbids exactly that**: "Changing any choice in SPEC section 2 requires a new
ADR and a SPEC update." Editing 0001's Decision/Consequences to reverse the
deploy model while leaving that bullet intact would ship an ADR that contradicts
its own process rule (review finding 1, HIGH).

An ADR is normally an append-only historical record, and the purist move is a
superseding ADR. The user has deliberately chosen the in-place path, so the
design honors it **and removes the contradiction in the same edit**: the ADR's
own policy bullet is amended to sanction in-place updates ("updating this ADR or
adding a superseding one"), and a **dated reversal note** is appended so the
history of the change is explicit even without a new file. AGENTS.md's "record
any agreed change in `docs/adr/`" is satisfied by the dated note. **Chosen:
in-place edit of ADR 0001 with (1) the amended policy bullet and (2) a dated
reversal note**, so the artifact is internally consistent rather than
self-contradictory. The exact three-part edit is pinned in the Files-to-modify
table row for `docs/adr/0001-technology-choices.md`.

---

## Files to create

### Argo CD Applications + ClusterSecretStore — `deploy/argocd/`

New directory mirroring the `deploy/k8s` layout. This is a **cluster-scoped,
register-once-per-cluster** kustomization — it has **no `namespace:`
transformer**, so cluster-scoped CRs render un-namespaced.

- **`deploy/argocd/application-dev.yaml`** — `apiVersion: argoproj.io/v1alpha1`,
  `kind: Application`, `metadata.namespace: argocd` (the conventional Argo CD
  install namespace; documented as replaceable). `spec.project: default`
  (documented replaceable). `spec.source.repoURL:
  https://github.com/PLACEHOLDER_GITHUB_ORG/k8s-fastapi-starter.git`,
  `spec.source.path: deploy/k8s/overlays/dev`, `targetRevision: HEAD`.
  `spec.destination.server: https://kubernetes.default.svc`,
  `namespace: fastapi-starter-dev`. `syncPolicy.automated`
  (`prune: true`, `selfHeal: true`) and `syncOptions: [CreateNamespace=true]`.
- **`deploy/argocd/application-prod.yaml`** — same shape, path
  `deploy/k8s/overlays/prod`, destination namespace `fastapi-starter-prod`.
  **`spec.syncPolicy` omits `automated` entirely** (review finding 7): no
  auto-sync, and because `prune`/`selfHeal` are sub-keys of `automated`, omitting
  it means **no auto-prune and no self-heal** either — the operator syncs prod
  **manually after review**, mirroring the "required reviewers for prod" posture.
  **`syncOptions: [CreateNamespace=true]` is retained** (kept outside `automated`,
  so it still applies to the manual sync). This is the only shape difference from
  the dev Application; documented inline in the manifest.
- **`deploy/argocd/clustersecretstore.yaml`** — `apiVersion:
  external-secrets.io/v1`, `kind: ClusterSecretStore`, `metadata.name:
  aws-secrets-manager` (cluster-scoped, no namespace). `spec.provider.aws:
  { service: SecretsManager, region: PLACEHOLDER_AWS_REGION }`;
  `auth` is **omitted** — rely on the ESO controller SA's IRSA (documented inline
  that ESO's controller SA carries the read-only role, like the Datadog Agent).
- **`deploy/argocd/kustomization.yaml`** — `resources:` lists
  `application-dev.yaml`, `application-prod.yaml`, `clustersecretstore.yaml`.
  **No `namespace:` field** (cluster-scoped resources). This kustomization is
  rendered/validated by `k8s-check` (kubeconform only; see Quality gate) but is
  **not** referenced by the app overlays.
- Argo CD prose (register with your platform's instance;
  `repoURL`/`project`/namespaces are placeholders) goes in `docs/deployment.md`,
  **not** a `deploy/argocd/README.md`, to avoid a second source of truth.

### ExternalSecret per overlay — `deploy/k8s/overlays/{dev,prod}/`

- **`deploy/k8s/overlays/dev/externalsecret.yaml`** — `apiVersion:
  external-secrets.io/v1`, `kind: ExternalSecret`, **`metadata.name:
  app-secrets-external`** (deliberately distinct from the Secret it produces — a
  shared `app-secrets` name across two kinds in one namespace is a
  `kubectl get` / bootstrap-race readability trap; review finding 6).
  `spec.refreshInterval: 1h`. `spec.secretStoreRef: { name: aws-secrets-manager,
  kind: ClusterSecretStore }`. **`spec.target.name: app-secrets`** (unchanged —
  so `deployment.yaml`/`migration-job.yaml` `secretRef: app-secrets` keeps
  resolving to the Secret, not the CR), `creationPolicy: Owner` (ESO owns/creates
  the `app-secrets` Secret). `spec.dataFrom[0].extract.key:
  k8s-fastapi-starter/dev/app-secrets` — pulls the whole JSON entry, whose
  `DATABASE_URL`/`REDIS_URL` keys become the Secret's keys. **Pointer only: zero
  secret material.**
- **`deploy/k8s/overlays/prod/externalsecret.yaml`** — identical but
  `extract.key: k8s-fastapi-starter/prod/app-secrets`.

Both files are added to their overlay `kustomization.yaml` `resources:`. Because
they live in the overlay, the overlay `namespace:` transformer correctly stamps
`fastapi-starter-{dev,prod}` on them (namespaced CRs — desired).

### Backup CronJob + ServiceAccount — `deploy/k8s/`

- **`deploy/k8s/base/backup-serviceaccount.yaml`** — `ServiceAccount` named
  `backup`, annotated `eks.amazonaws.com/role-arn: PLACEHOLDER_BACKUP_ROLE_ARN`
  (IRSA, S3/KMS write). **`automountServiceAccountToken: true`** — IRSA requires
  the projected web-identity token to be mounted so the AWS CLI can
  `AssumeRoleWithWebIdentity` via STS; `false` (as on the `app` SA, which never
  calls AWS) would make every backup fail to authenticate (review finding 1).
  This is the first workload in the repo that calls AWS, so it is the first SA
  that needs the token. **kube-linter result is NOT pre-decided (review finding
  4):** during implementation, run kube-linter (pinned **0.8.3**, the repo's
  empty `exclude: []`) against the rendered dev/prod overlays **with the CronJob
  present** and confirm **zero new diagnostics BEFORE** asserting the config is
  clean. The likeliest check to fire is `default-service-account` /
  SA-token-automount around `automountServiceAccountToken: true`. **If a default
  check fires, add the exclusion to `deploy/k8s/.kube-linter.yaml` WITH a written
  reason per SPEC §14.9 in the same change** (e.g. the backup SA mounts its token
  because IRSA requires the projected web-identity token for STS
  `AssumeRoleWithWebIdentity`). Do not pre-commit to "no exclusion needed" and do
  not assert clean without having run the linter on the CronJob-bearing render.
- **`deploy/k8s/base/backup-cronjob.yaml`** — `apiVersion: batch/v1`,
  `kind: CronJob`, `metadata.name: db-backup`. `schedule: "0 3 * * *"`
  (daily, documented UTC). `concurrencyPolicy: Forbid`,
  `successfulJobsHistoryLimit`/`failedJobsHistoryLimit` set. Pod template mirrors
  the migration Job's security posture (so kube-linter passes):
  - **Pod template `metadata.labels` MUST include
    `app.kubernetes.io/name: k8s-fastapi-starter`** (review finding 3). This
    label is the **entire basis** on which the `app` NetworkPolicy's
    `podSelector` selects the backup pod; without it, the policy does not match
    the pod and its egress to Postgres (5432), S3/STS/KMS/Secrets Manager (443)
    is denied, so **every backup silently fails**. The **full label set the
    backup pod carries** is:
    `app.kubernetes.io/name: k8s-fastapi-starter` (load-bearing — NetworkPolicy
    selection) **and** `app.kubernetes.io/component: backup` (identity/filtering
    only). The pod must NOT be labeled by component alone.
  - pod `securityContext`: `runAsNonRoot: true`, `runAsUser/Group/fsGroup: 10001`,
    `seccompProfile: RuntimeDefault`.
  - `serviceAccountName: backup` (NOT `app` — it needs the IRSA SA).
  - container `securityContext`: `allowPrivilegeEscalation: false`,
    `readOnlyRootFilesystem: true`, `runAsNonRoot: true`,
    `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`.
  - `resources.requests/limits` (cpu/memory), `restartPolicy: OnFailure`,
    `backoffLimit`, `activeDeadlineSeconds`, `ttlSecondsAfterFinished`.
  - A writable `/tmp` `emptyDir` (`sizeLimit`) so `readOnlyRootFilesystem: true`
    holds while streaming works.
  - **Image:** `PLACEHOLDER_BACKUP_IMAGE`. The app image is `python:3.13-slim`
    (confirmed by reading `Dockerfile`), which ships neither `pg_dump` nor the
    AWS CLI, and a Pod's containers do not run sequentially, so a single
    dedicated image carrying the Postgres 17 client (`pg_dump`) **and** AWS CLI
    v2 is required. The template does not build this image; the consumer supplies
    or mirrors it and should pin it by digest (documented in `docs/deployment.md`).
  - **Command:** stream `pg_dump "$DATABASE_URL" | gzip` to
    `aws s3 cp - s3://PLACEHOLDER_BACKUP_BUCKET/${BACKUP_PREFIX}/db/<timestamp>.sql.gz
    --sse aws:kms --sse-kms-key-id PLACEHOLDER_BACKUP_KMS_KEY_ID`. Streaming
    through the pipe needs no large temp file. The S3 prefix comes from a
    **`BACKUP_PREFIX` container env var** so dev and prod dumps never collide in
    the bucket (review finding 8): the **base** CronJob sets `BACKUP_PREFIX: base`
    (a neutral placeholder), and **each overlay patches it** to its own env name
    (`dev` / `prod`) — this patch is listed in the overlay Files-to-modify rows
    below and is mandatory, not optional, so base-rendered dumps never share a
    prefix across environments.
  - `DATABASE_URL` from `envFrom: - secretRef: { name: app-secrets }` (the
    ESO-managed Secret — no new secret plumbing). `DD_TRACE_ENABLED: "false"`
    like the migration Job.
- Both added to `deploy/k8s/base/kustomization.yaml` `resources:`. Each overlay
  **must** patch `BACKUP_PREFIX` to its env name (`dev`/`prod`) — a mandatory
  per-env S3-prefix patch (review finding 8), listed in the overlay rows of
  Files-to-modify. Overlays may additionally patch the schedule; dev stays
  enabled (document that a consumer can set `spec.suspend: true` in the dev
  overlay if desired). Keeping the CronJob in base means both overlays get it and
  the overlay `namespace:` transformer namespaces it correctly.

> **Hard requirements for the backup CronJob:** daily schedule; `pg_dump` of
> `DATABASE_URL` streamed to the S3 bucket with KMS-CMK server-side encryption;
> kube-linter-clean security context (non-root, dropped caps, read-only rootfs,
> resource limits, seccomp); the dedicated `PLACEHOLDER_BACKUP_IMAGE` pinned by
> digest; a `backup` SA with `automountServiceAccountToken: true` + IRSA
> annotation; pod template labels including
> `app.kubernetes.io/name: k8s-fastapi-starter` so the `app` NetworkPolicy
> selects it; bucket/key/role as `PLACEHOLDER_` tokens.

### Vendored CRD schemas — `deploy/k8s/crd-schemas/`

For **deterministic, offline** kubeconform validation of the three new CRD kinds
(review finding 4), vendor their JSON schemas instead of relying on a remote
catalog whose coverage/versioning can change and whose resolved-but-404 URL can
hard-fail `-strict`. Create:

- `deploy/k8s/crd-schemas/externalsecret_v1.json`
- `deploy/k8s/crd-schemas/clustersecretstore_v1.json`
- `deploy/k8s/crd-schemas/application_v1alpha1.json`

Each is the published OpenAPI/JSON schema for that CRD (sourced once from the
datreeio CRDs-catalog or the upstream CRD `openAPIV3Schema`, then committed).
File naming matches kubeconform's `{{.ResourceKind | toLower}}_{{.ResourceAPIVersion}}.json`
template so a single `-schema-location` directory resolves all three. A short
`deploy/k8s/crd-schemas/README.md` records the provenance and apiVersions so a
maintainer can refresh them, and notes that the `-schema-location` template
**intentionally omits the API group** (safe because each kind is unique across
groups; a future CRD sharing a kind would need the group re-added). (Alternative considered: the remote datreeio
catalog `-schema-location` URL — rejected for the network dependency and the
unverified per-apiVersion coverage; vendoring is hermetic and matches CI's
otherwise self-contained posture.)

### Bootstrap script — `scripts/bootstrap_backups.py`

Provisions the **S3 backup bucket** and the **backup KMS CMK** (backups only —
explicitly NOT a Secrets Manager CMK). Conventions match
`scripts/check_sops_encrypted.py`: module docstring, typed functions
(`mypy --strict`), `argparse` CLI, `main(argv: list[str]) -> int`, stdlib +
`boto3`. Behavior:

- Reads bucket name, KMS alias, and region from CLI args. **Defaults are the
  `PLACEHOLDER_` tokens** (`--bucket PLACEHOLDER_BACKUP_BUCKET`, etc.) so the
  token strings appear in the file and `rg PLACEHOLDER_` finds them pre-init.
- Idempotent create-if-absent for the bucket, the CMK, and the alias; enable
  bucket versioning, block public access, a default SSE-KMS encryption config
  using the new CMK, and a lifecycle rule expiring objects after
  **`--retention-days` (default `30`, documented as adjustable)** — review
  finding 6. The concrete default makes the `--dry-run` plan **deterministic**,
  which the `--dry-run` unit test depends on (the test asserts the printed plan
  names a 30-day expiry unless overridden).
- **Safety default: `--dry-run` is the default (prints the plan); `--apply` is
  required to mutate AWS.** `--apply` combined with a still-`PLACEHOLDER_`-valued
  bucket (or key/region) is a **hard error** (refuse to create a literally-named
  `PLACEHOLDER_` resource); `--dry-run` against the placeholder default prints
  the plan without erroring. Bucket name validated against S3 naming rules
  (lowercase, 3–63 chars, DNS-ish); region non-empty.
- `boto3` + `boto3-stubs[s3,kms]` added as **dev** deps via `uv add --dev` (not
  app-runtime; keeps `mypy --strict` clean without `Any`). Never hand-edit
  `uv.lock`.
- **Not** wired into any `poe check`/`check-all` sequence (it touches live AWS);
  run by an operator once per account. Optionally expose
  `poe backup-bootstrap` as a convenience (dry-run default). Documented in
  `docs/deployment.md`.

### Documentation (new/rewritten)

- **`docs/deployment.md`** — rewrite for GitOps: Argo CD replaces the CI apply
  job; the two `Application` manifests + `ClusterSecretStore`; the sync flow; the
  migration-Job PreSync hook; `sync-secrets` workflow; `bootstrap_backups.py` and
  the backup CronJobs; the break-glass manual `kubectl apply -k` note (replacing
  the removed `deploy.yml`); and the **two documented rough edges** (below).
  **Next to the "ESO assumed pre-installed, cluster-level" note, state the hard
  minimum ESO version floor of v0.10.0** (when the `external-secrets.io/v1` API
  group was promoted) so a consumer on an older ESO knows the manifests at `v1`
  will be rejected and must upgrade ESO first (review finding 1). The floor
  documented here must match the manifest `apiVersion` and the vendored
  `_v1.json` schema filenames.
- **`docs/secrets.md`** — rewrite "How it works" / "Apply a secret" / "Open
  questions": the chain is now SOPS (git) → CI `sync-secrets` → Secrets Manager →
  ESO → `app-secrets` Secret. Mark Q4 decided (point at ADR 0001). Document the
  rotation rough edge. **Flag the break-glass footgun** (review finding 9): a
  manual `sops --decrypt … | kubectl apply -f -` still works but is **transient**
  — ESO's `refreshInterval: 1h` (and any reconcile) will overwrite the Secret
  with the Secrets Manager value; real break-glass is pushing to Secrets Manager
  (or editing the `ExternalSecret`), not kubectl-applying a decrypted Secret.
- **`docs/architecture.md`** — add Argo CD + ESO + the backup CronJob to the
  "Kubernetes shape" / component description.

---

## Files to modify (exhaustive)

| File                                          | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `docs/adr/0001-technology-choices.md`         | Three coordinated edits (keep ADR style, keep **"Accepted"** status, **do NOT create a new ADR** — see "ADR process reconciliation" below). (a) In the **Decision** summary, change the Kustomize/SOPS line to add Argo CD GitOps and the SOPS→Secrets Manager→ESO secrets chain. (b) Replace the Consequences bullet "Deployment is a CI job, not GitOps. Revisit this if the organization standardizes on Argo CD or Flux." with: **deployment is Argo CD GitOps; secrets flow SOPS (git source of truth) → AWS Secrets Manager (synced by the `sync-secrets` CI job) → External Secrets Operator.** (c) **Amend the self-contradictory Consequences bullet** "Changing any choice in SPEC section 2 requires a new ADR and a SPEC update." to permit in-place updates, e.g.: "Changing a choice in SPEC §2 requires updating this ADR (or adding a superseding one) and the SPEC, with the rationale recorded here; do not change a choice quietly." (d) Append a dated reversal note under Consequences, e.g.: "**2025-xx-xx:** deploy model reversed from CI-apply to Argo CD GitOps; secrets runtime changed to SOPS→AWS Secrets Manager→ESO (SOPS remains the git source of truth); this ADR updated in place per the amended policy above." Use the current date at implementation time; do not fabricate. |
| `SPEC.md` §1                                  | Remove GitOps from non-goals; note GitOps via Argo CD is adopted (point at ADR 0001 and §10).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `SPEC.md` §2 (table)                          | Add a **GitOps controller = Argo CD** row (was a non-goal). Change the **Secrets** row's choice to "SOPS (git source of truth) → AWS Secrets Manager (synced by CI) → External Secrets Operator". Keep the table's three-column format and the "why not alternatives" text.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `SPEC.md` §3                                  | Add to the repo layout tree + infra list: `deploy/argocd/` (Applications + ClusterSecretStore), per-overlay `externalsecret.yaml`, `deploy/k8s/base/backup-cronjob.yaml` + `backup-serviceaccount.yaml`, `deploy/k8s/crd-schemas/`, `scripts/bootstrap_backups.py`, the S3 backup bucket + backup CMK, and the daily backup CronJobs.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `SPEC.md` §6 (table)                          | kubeconform row: note CRD schemas (Argo CD `Application`, ESO `ExternalSecret`/`ClusterSecretStore`) are validated via vendored schemas under `deploy/k8s/crd-schemas/` fed with `-schema-location`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `SPEC.md` §9                                  | Add the CI sync-to-Secrets-Manager step and ESO materialization; state SOPS stays the git source of truth; note the default Secrets Manager key is intentional (two-role isolation: CI write-only, ESO read-only) and the only CMK is for backups.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `SPEC.md` §10                                 | Replace the "**Deployment:** a `deploy` job … decrypts SOPS … applies the overlay" paragraph with: Argo CD applies manifests (GitOps); the migration-Job-before-Deployment ordering is now an Argo CD PreSync hook; add the new **`sync-secrets`** workflow (own file, path-filtered, OIDC role + the extra `secretsmanager:*` IAM perms). Note `deploy.yml` is removed. Keep the Datadog-at-deploy label note and the action-pinning note.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `SPEC.md` §13                                 | Mark **Q3** (item 3, deploy target / GitOps-vs-CI) and **Q4** (item 4, SOPS-vs-ESO) **"Decided — …"** in the exact style item 9 uses, each pointing at ADR 0001. **Also append to item 5** (AWS KMS ARNs and CI auth — left *open*, not marked Decided): a note that the CI OIDC role now **additionally** needs `secretsmanager:PutSecretValue`, `secretsmanager:CreateSecret`, and `secretsmanager:DescribeSecret` scoped to the app's secret ARNs (`arn:aws:secretsmanager:*:*:secret:k8s-fastapi-starter/*/app-secrets-*`), on top of the existing `kms:Decrypt` for SOPS, cross-referencing §9/§10 and `.github/workflows/sync-secrets.yml`. This keeps the open question coherent with the new IAM dependency the design introduces rather than silently widening the role (review finding 5).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |  |
| `deploy/k8s/base/kustomization.yaml`          | Add `backup-serviceaccount.yaml` and `backup-cronjob.yaml` to `resources:`. Rewrite the header secrets caveat comment to: the `app-secrets` Secret is **materialized by ESO from the per-overlay `ExternalSecret`** (not applied by CD); it is not listed here because the `ExternalSecret` lives in the overlays. (The base lists **no** secret/ExternalSecret resource itself.)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `deploy/k8s/base/migration-job.yaml`          | Add Argo CD annotations: `argocd.argoproj.io/hook: PreSync`, `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation`, `argocd.argoproj.io/sync-wave: "-1"`. **Rewrite the header comment** to describe the PreSync-hook lifecycle (hook recreated each sync via BeforeHookCreation; the SHA-append dance is gone; overlays still rename the Job per env). Keep spec/security/resources otherwise unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `deploy/k8s/base/networkpolicy.yaml`          | Add `- protocol: TCP; port: 443` to the **existing any-IP (no-`to:`) egress rule** (the one with 5432/6379/8126/8125) with a reason comment: the backup CronJob pod carries the `app.kubernetes.io/name: k8s-fastapi-starter` label, so this policy selects it, and it needs 443 for S3, STS (IRSA `AssumeRoleWithWebIdentity`), KMS, and Secrets Manager endpoints. **Do NOT** put 443 under the `kube-system`-scoped DNS rule. ESO runs cluster-level and is **not** selected by this policy, so the rule is for the backup pod only. **Add a one-line note (in the existing `TIGHTEN per environment` comment) that any future tightening of this any-IP rule to a `to:` selector MUST preserve the backup pod's egress to Postgres (5432), S3, STS, KMS, and Secrets Manager (443)** — otherwise backups silently fail (review finding 3).                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `deploy/k8s/overlays/dev/kustomization.yaml`  | Add `externalsecret.yaml` to `resources:`; **remove** the "BEFORE DEPLOY … sops decrypt … / secrets.dev.yaml is intentionally NOT listed" comment block; replace with a short note that `app-secrets` is now materialized by ESO from `externalsecret.yaml` and that rotation is `sops` → commit → CI → Secrets Manager → ESO. **Add a mandatory patch setting the `db-backup` CronJob container env `BACKUP_PREFIX: dev`** (review finding 8) so dev dumps land under `s3://…/dev/` and never collide with prod.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `deploy/k8s/overlays/prod/kustomization.yaml` | Same as dev (path = prod; keep the existing prod-specific patches/pdb). **The mandatory `BACKUP_PREFIX` patch sets `prod`** so prod dumps land under `s3://…/prod/` (review finding 8).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `.github/workflows/deploy.yml`                | **Delete** (superseded by Argo CD GitOps; break-glass is documented in `docs/deployment.md`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `pyproject.toml`                              | Add `boto3` + `boto3-stubs[s3,kms]` as dev deps (via `uv add --dev`, which updates `uv.lock`). Rewrite the `k8s-check` task (see Quality gate): render dev/prod/argocd; run kubeconform **twice** — pass (a) lenient over all three renders (`-ignore-missing-schemas` backstop, `-schema-location default` + `-schema-location deploy/k8s/crd-schemas`), pass (b) scoped to the CRD-bearing renders with `-ignore-missing-schemas` removed and `-schema-location` pointing only at `deploy/k8s/crd-schemas` so a missing/mis-named vendored schema for the three known kinds fails the build (review finding 2); kube-linter on dev/prod only. Optionally add a `backup-bootstrap` convenience task (dry-run default).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `.template/placeholders.yml`                  | Register new `PLACEHOLDER_` tokens (below); extend `PROJECT_SLUG.replace_in` with `deploy/argocd/**` and `.github/workflows/sync-secrets.yml`; update the `external` entries' `referenced_in` from `deploy.yml` to `sync-secrets.yml`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `deploy/k8s/.kube-linter.yaml`                | **Change only if verification requires it (review finding 4).** During implementation, run kube-linter (pinned 0.8.3, empty `exclude: []`) against the rendered dev/prod overlays **with the backup CronJob present** and confirm zero new diagnostics before asserting clean. If a default check fires (likeliest `default-service-account` / SA-token-automount on `automountServiceAccountToken: true`), add the exclusion here **with a written reason** per SPEC §14.9 in the same change. Do not pre-commit to "no change".                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `mkdocs.yml`                                  | **No nav change** (editing existing pages only).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

New file (not a modify): **`.github/workflows/sync-secrets.yml`** (below).

---

## CI `sync-secrets` workflow (`.github/workflows/sync-secrets.yml`)

**This is a new, standalone workflow file — not a job added to `ci.yml`.** GitHub
Actions `paths:`/`paths-ignore:` are **workflow-level** keys under `on.<event>`,
not job-level; adding `paths:` to `ci.yml`'s `on:` would gate **all** of CI
(lint/test/k8s/docs/image) on a secrets file changing, breaking CI for ordinary
PRs (review finding 2). A dedicated workflow is the clean, correct mechanism and
leaves `ci.yml` untouched.

```yaml
on:
  push:
    branches: [main]
    paths: ['deploy/k8s/overlays/**/secrets.*.yaml']
  pull_request:
    paths: ['deploy/k8s/overlays/**/secrets.*.yaml']
```

- **Permissions:** workflow-level `contents: read`; the job adds
  `id-token: write` (OIDC). Least privilege.
- **Job `sync-secrets`:** `strategy.matrix.env: [dev, prod]`. Steps:
  1. `actions/checkout` (same pinned SHA as `ci.yml`).
  2. **Skip-if-absent guard:** if `deploy/k8s/overlays/<env>/secrets.<env>.yaml`
     does not exist (the template default — only `secrets.example.yaml` is
     committed), log `skipping <env>: no secrets file` and succeed. Only
     `secrets.example.yaml` ships, so on the template this workflow is a no-op.
  3. Install sops (same `SOPS_VERSION` pinned-binary pattern the old `deploy.yml`
     used — reproduce it here since `deploy.yml` is deleted). **`yq` is the
     mikefarah build** (review finding 5): it is **preinstalled on
     `ubuntu-latest`**, so no explicit install step is required; if reproducible
     builds are wanted, pin a `yq` binary the same way the sops step pins its
     version. The extraction expression relies on the **`@base64d` operator,
     which is mikefarah-`yq`-specific** (the Python `yq` does not provide it), so
     the flavor matters — do not substitute Python `yq`.
  4. `aws-actions/configure-aws-credentials` (same pinned SHA as the old
     `deploy.yml`) assuming `vars.AWS_DEPLOY_ROLE_ARN` in `vars.AWS_REGION`.
     **Gate the credential + AWS-write steps** with
     `if: github.event_name == 'push' && github.ref == 'refs/heads/main'` so fork
     PRs never mutate AWS. On PRs, only a dry-run decrypt + key-shape validation
     runs (no AWS).
  5. Decrypt + validate + push: `sops -d` the file; extract the value map and
     build one flat JSON object (see "JSON extraction and the write→read
     contract" below); **validate the extracted keys equal exactly
     `{DATABASE_URL, REDIS_URL}`** (fail with a clear message otherwise);
     then write to Secrets Manager with the **put-first / create-on-404**
     mechanism (pinned below). Keep plaintext in `$RUNNER_TEMP` only, delete on
     exit, never log, mask.

     **put/create mechanism (pinned, no pre-probe — review finding 2):** run
     `aws secretsmanager put-secret-value --secret-id
     k8s-fastapi-starter/<env>/app-secrets --secret-string file://<tmp>` first;
     **only if it exits with `ResourceNotFoundException`**, run `aws
     secretsmanager create-secret --name k8s-fastapi-starter/<env>/app-secrets
     --secret-string file://<tmp>` (first-run bootstrap). Do **not** pre-probe
     with `describe-secret` — catching the error from the `put` avoids a TOCTOU
     window and is safe with the dev/prod matrix running in parallel (distinct
     secret ids). Capture the AWS CLI stderr and branch on the error code string
     rather than exit status alone. One further non-normal case is explicitly
     fatal: if `put-secret-value` returns `InvalidRequestException` (the secret
     is **scheduled for deletion**), fail the job with a message instructing the
     operator to `aws secretsmanager restore-secret --secret-id …` first — do not
     attempt `create-secret` (it would also fail) and do not silently swallow it.
- **IAM comment (code comment above the job):** the OIDC role needs
  `secretsmanager:PutSecretValue`, `secretsmanager:CreateSecret`, and
  `secretsmanager:DescribeSecret` scoped to
  `arn:aws:secretsmanager:*:*:secret:k8s-fastapi-starter/*/app-secrets-*`, in
  addition to the `kms:Decrypt` it already has for SOPS. Additive to the existing
  deploy role.
- **Action pinning:** full SHAs (security-sensitive), reusing the exact pins from
  `ci.yml` / the former `deploy.yml` so Dependabot keeps them in sync.

### JSON extraction and the write→read contract (review findings 3 & 4)

The bytes written to Secrets Manager and the bytes ESO reads back are a single
load-bearing contract that **no in-cluster or render-time check validates**
(kustomize/kubeconform never see the secret values; `envFrom` does not require
specific keys at render time). The `sync-secrets` key-shape check is therefore
**the only guard**, so it must be precise:

- **Extract the value map regardless of field.** A SOPS-encrypted `Secret` may
  legitimately carry its values under `stringData` (plaintext) **or** `data`
  (base64). The template default (`secrets.example.yaml`) uses `stringData`, but
  the extractor must not hard-code it or a valid `data:`-based file would fail
  with a confusing "missing key" error. Use an expression that prefers
  `stringData` and falls back to base64-decoded `data`, e.g.:

  ```sh
  sops -d "secrets.<env>.yaml" \
    | yq -o=json '.stringData // (.data | map_values(@base64d))' > "$tmp/secret.json"
  ```

  `yq` here is the **mikefarah build** (preinstalled on `ubuntu-latest`); the
  `@base64d` operator in the `data` fallback is mikefarah-specific and is not
  available in the Python `yq`, so the flavor is load-bearing (review finding 5).
  This selects **only the value map** — `apiVersion`, `kind`, `metadata`, and
  `type` are never included, so no non-secret cruft is written to Secrets Manager
  (which would otherwise surface as spurious `app-secrets` keys via ESO's
  `extract`).
- **Validate the extracted keys equal exactly `{DATABASE_URL, REDIS_URL}`.**
  Compare the sorted key set of `$tmp/secret.json` against the literal set; a
  missing **or** extra key fails the job with a message naming the offending key
  (never its value). This runs on PRs too (dry-run, no AWS), so breakage is
  caught before merge.
- **The result is a flat JSON object** `{"DATABASE_URL": "...", "REDIS_URL":
  "..."}`. ESO's `dataFrom[0].extract` maps each top-level JSON key to one
  `app-secrets` Secret key 1:1, which `envFrom: secretRef: app-secrets` then
  exposes as env vars. Because this write→read mapping is only ever as correct as
  the write-side check, the design treats the key-shape validator as
  **load-bearing, not cosmetic** (see Invariants and ownership).

---

## Quality gate: kubeconform + kube-linter (rewritten `k8s-check`)

The task requires kubeconform to **recognize** the new CRDs (today
`-ignore-missing-schemas` skips them silently). The rewritten `k8s-check` shell
task:

```sh
set -eu
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# Workload overlays: validated by BOTH kubeconform and kube-linter.
for env in dev prod; do
  kubectl kustomize deploy/k8s/overlays/$env > "$tmp/$env.yaml"
done
# Cluster-scoped Argo CD Applications + ClusterSecretStore: kubeconform ONLY
# (kube-linter's workload checks are meaningless for Application/Store CRs).
kubectl kustomize deploy/argocd > "$tmp/argocd.yaml"

# PASS (a) — the normal, lenient pass. -ignore-missing-schemas is the backstop
# for ANY other CRD a consumer may add; core + the three vendored CRD schemas
# resolve here too.
kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default \
  -schema-location 'deploy/k8s/crd-schemas/{{ .ResourceKind | toLower }}_{{ .ResourceAPIVersion }}.json' \
  "$tmp/dev.yaml" "$tmp/prod.yaml" "$tmp/argocd.yaml"

# PASS (b) — the DURABLE "resolves, not skips" gate. Scoped to only the
# CRD-bearing renders, with -ignore-missing-schemas REMOVED and -schema-location
# pointing ONLY at the vendored CRD directory (no `default`). kubeconform here
# errors on anything it cannot resolve, so if a vendored schema for the three
# known kinds (Application, ExternalSecret, ClusterSecretStore) is missing,
# mis-named, or version-drifted, THIS pass fails the build every time. The
# argocd render carries Application + ClusterSecretStore; the overlays carry
# ExternalSecret.
kubeconform -strict -summary \
  -schema-location 'deploy/k8s/crd-schemas/{{ .ResourceKind | toLower }}_{{ .ResourceAPIVersion }}.json' \
  -skip 'Namespace,ServiceAccount,ConfigMap,Secret,Service,Deployment,Job,CronJob,NetworkPolicy,PodDisruptionBudget,HorizontalPodAutoscaler' \
  "$tmp/argocd.yaml" "$tmp/dev.yaml" "$tmp/prod.yaml"

kube-linter lint "$tmp/dev.yaml" "$tmp/prod.yaml"
```

Key points (addressing review findings 2 and 4):

- **Vendored schemas, not the remote catalog** — `-schema-location
  deploy/k8s/crd-schemas/...` resolves the three new kinds from committed JSON.
  No network dependency; CI result is deterministic.
- **Two kubeconform passes make "resolves, not skips" DURABLE in CI, not a
  one-off manual run (review finding 2).** Pass (a) keeps
  `-ignore-missing-schemas` as the backstop for other CRDs. Pass (b) is a
  **second, scoped** invocation over only the CRD-bearing renders with
  `-ignore-missing-schemas` **removed** and `-schema-location` pointing **only**
  at `deploy/k8s/crd-schemas/` (no `-schema-location default`). The core
  Kubernetes kinds in those renders are excluded from pass (b) via `-skip`
  (they have no vendored schema and are already validated by pass (a)), so the
  only kinds pass (b) must resolve are the three vendored CRDs. If any of
  `Application`, `ExternalSecret`, or `ClusterSecretStore` is unresolved
  (missing/mis-named/version-drifted vendored schema, or an apiVersion drift per
  finding 1), pass (b) **errors and fails the build** — the exact silent-skip
  regression this task exists to prevent is now caught on every CI run, not by a
  human remembering to drop a flag once. This lives in the committed
  `poe k8s-check` task, so local runs match CI.
- **kube-linter runs on dev/prod only**, never on `deploy/argocd` — the explicit
  file list prevents Argo CD `Application` / `ClusterSecretStore` CRs from being
  fed to kube-linter's workload checks.
- **kubeconform runs on all three**, so the Applications, the ClusterSecretStore,
  and both ExternalSecrets are schema-checked.
- The `ci.yml` `k8s` job already installs kubeconform/kube-linter and calls
  `uv run poe k8s-check`; it needs **no change** beyond what the task rewrite
  provides (it checks out the repo, so the vendored schemas are present).
- **Verify in the gate** that `deploy/argocd` renders the `ClusterSecretStore`
  with **no `metadata.namespace`** (confirms finding 3 is resolved).
- **Schema-location template, group intentionally omitted (review finding 6).**
  The template `{{ .ResourceKind | toLower }}_{{ .ResourceAPIVersion }}.json`
  deliberately **drops the API group** because the three CRD kinds here
  (`application`, `externalsecret`, `clustersecretstore`) are each unique across
  groups, so kind+version uniquely names a file; a one-line comment in both
  `pyproject.toml` (above the `k8s-check` task) and `deploy/k8s/crd-schemas/README.md`
  records this and warns that a future CRD sharing a kind would need the group
  re-added. `.ResourceAPIVersion` resolves to the **version segment only**
  (`v1`, `v1alpha1`), which is why the vendored files are named
  `externalsecret_v1.json`, `clustersecretstore_v1.json`,
  `application_v1alpha1.json`.
- **Prove the three kinds *resolve*, not *skip* — DURABLY, in CI (review
  findings 2 and 6).** Because pass (a) keeps `-ignore-missing-schemas`, on its
  own a mis-named or missing vendored schema would **silently skip** the CRD
  instead of validating it — the exact failure mode this task exists to fix.
  Pass (b) above closes this permanently: it is a committed, scoped kubeconform
  invocation over the CRD-bearing renders with `-ignore-missing-schemas`
  **removed** and `-schema-location` pointing **only** at
  `deploy/k8s/crd-schemas/`, so an unresolved `Application`/`ExternalSecret`/
  `ClusterSecretStore` schema **fails `poe k8s-check` (and therefore CI) every
  time**. This is no longer a one-off human action that gets reverted; it is a
  permanent asserted gate output (see Verification gates). A later apiVersion
  bump, schema refresh, or kind rename that breaks resolution regresses to a
  **red** build, not a green one.

`kube-linter`: the backup CronJob and `backup` SA follow the existing security
posture, but the result is **verified, not assumed** (review finding 4). During
implementation, run `kube-linter lint` (pinned **0.8.3**, the repo's empty
`exclude: []`) against the rendered dev/prod overlays **with the CronJob
present** and confirm zero new diagnostics before asserting clean. If a default
check fires — likeliest `default-service-account` / token-automount on the
backup SA's `automountServiceAccountToken: true` — add the exclusion to
`deploy/k8s/.kube-linter.yaml` **with a written reason** (SPEC §14.9) in the same
change. Otherwise, fix the manifest. Do not pre-commit to "no exclusion needed".

---

## New `PLACEHOLDER_` tokens and registry

All added to `.template/placeholders.yml`. All are valid plain strings so the
template builds and `rg PLACEHOLDER_` lists them; none are fabricated real
values.

| Token                           | Meaning                                                      | Section | Files                                                                               |
| ------------------------------- | ------------------------------------------------------------ | ------- | ----------------------------------------------------------------------------------- |
| `PLACEHOLDER_BACKUP_BUCKET`     | S3 bucket name for DB backups                                | `fill`  | `deploy/k8s/base/backup-cronjob.yaml`, `scripts/bootstrap_backups.py` (default arg) |
| `PLACEHOLDER_BACKUP_KMS_KEY_ID` | KMS CMK id/alias for backup encryption (backups only)        | `fill`  | `deploy/k8s/base/backup-cronjob.yaml`, `scripts/bootstrap_backups.py`               |
| `PLACEHOLDER_BACKUP_ROLE_ARN`   | IRSA role ARN the `backup` SA assumes (S3/KMS write)         | `fill`  | `deploy/k8s/base/backup-serviceaccount.yaml`                                        |
| `PLACEHOLDER_BACKUP_IMAGE`      | Dedicated backup image: `pg_dump` (pg17 client) + AWS CLI v2 | `fill`  | `deploy/k8s/base/backup-cronjob.yaml`                                               |

**Fill-vs-external split (review finding 10), stated for registry consistency.**
`PLACEHOLDER_BACKUP_ROLE_ARN` is registered as **`fill`** (not `external`),
because it is written into `backup-serviceaccount.yaml` as a literal annotation
value that must be a valid in-repo string — matching how `.sops.yaml`'s
region/account/key id are `fill`. This differs intentionally from the other IAM
role in this design, the CI/ESO deploy role, which stays **`external`**
(`vars.AWS_DEPLOY_ROLE_ARN`, a GitHub Environment variable set in the console,
never written into a committed file). The rule: an ARN baked into a manifest is
`fill`; an ARN consumed only via a GitHub `vars.*` reference is `external`. The
init agent should expect exactly one new in-repo role token and one existing
GitHub-var role.

**No `PLACEHOLDER_ARGOCD_NAMESPACE`** — the Argo CD namespace defaults to the
literal `argocd` and is documented as replaceable, avoiding an extra token.

**`bootstrap_backups.py` default/guard alignment (finding 10):** the `--bucket`
default **is** the literal string `PLACEHOLDER_BACKUP_BUCKET` (so `rg
PLACEHOLDER_` finds it pre-init and `--dry-run` works out of the box); `--apply`
with a still-placeholder bucket/key/region is a hard error. The token table and
the Input-validation section agree on this exact string.

**Reused existing tokens:** `PLACEHOLDER_GITHUB_ORG` (Application `repoURL`),
`PLACEHOLDER_AWS_REGION` (ClusterSecretStore region), `vars.AWS_DEPLOY_ROLE_ARN`
/ `vars.AWS_REGION` (sync-secrets workflow — external, no new token).

**`PROJECT_SLUG` `replace_in` extension.** The Secrets Manager entry name and
the Application manifests embed the slug `k8s-fastapi-starter`, a **rename**
target. The existing glob `deploy/k8s/**` already covers the new base/overlay
files, but **not** `deploy/argocd/**` or `.github/workflows/sync-secrets.yml`.
Add both to `PROJECT_SLUG.replace_in`. Keep `SPEC.md`/`README.md`/`AGENTS.md`/
`.template/` in `keep_as_is`.

**`external` `referenced_in` update:** change `AWS_DEPLOY_ROLE_ARN` and
`AWS_REGION` `referenced_in` from `.github/workflows/deploy.yml` (deleted) to
`.github/workflows/sync-secrets.yml`.

---

## Error handling (concrete, per failure point)

| Operation                                                        | Failure condition                     | Recoverable?                                | Caller sees                                                                                     | Logged                                 |
| ---------------------------------------------------------------- | ------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------- |
| `sync-secrets`: secrets file absent                              | Template default state                | Not an error                                | "skipping <env>: no secrets file"                                                               | Info                                   |
| `sync-secrets`: sops decrypt                                     | KMS denied / corrupt file             | Fatal (job fails)                           | Non-zero exit, GH `::error::`                                                                   | Yes — error line, **never plaintext**  |
| `sync-secrets`: key-shape validation                             | Missing/extra key in JSON             | Fatal (job fails)                           | Non-zero exit, clear message                                                                    | Yes — which key (no values)            |
| `sync-secrets`: `put-secret-value` → `ResourceNotFoundException` | Secret missing (first run)            | Recoverable                                 | Falls back to `create-secret` (no pre-probe)                                                    | Info                                   |
| `sync-secrets`: `put-secret-value` → `InvalidRequestException`   | Secret scheduled for deletion         | **Fatal (job fails)**                       | Non-zero exit; message: run `aws secretsmanager restore-secret --secret-id …` first             | Yes — AWS error code (no secret value) |
| `sync-secrets`: `put/create`                                     | IAM denied / throttled                | Fatal (job fails)                           | Non-zero exit                                                                                   | Yes — AWS error (no secret value)      |
| ESO: pull from Secrets Manager                                   | IRSA denied / key absent              | Recoverable (retries on `refreshInterval`)  | `ExternalSecret` status `SecretSyncError`; `app-secrets` not created/stale; pods fail readiness | ESO controller logs (cluster-level)    |
| Argo CD: PreSync migration Job fails                             | Migration error                       | Fatal for that sync                         | Sync marked failed; Deployment not updated                                                      | Argo CD UI + Job pod logs              |
| Argo CD: ExternalSecret not synced on first bootstrap            | New env, Secret absent                | **Rough edge — documented, not engineered** | Migration Job may start before Secret exists → CrashLoop until Secret appears                   | Argo CD + ESO logs                     |
| Backup CronJob: IRSA/STS assume fails                            | SA token not mounted / role untrusted | Recoverable (next schedule)                 | Failed Job; `aws` auth error                                                                    | Job pod logs                           |
| Backup CronJob: `pg_dump` fails                                  | DB unreachable / auth                 | Recoverable (next schedule; `backoffLimit`) | Failed Job in history                                                                           | Job pod logs                           |
| Backup CronJob: S3 upload fails                                  | IAM / network / KMS denied            | Recoverable (next schedule)                 | Failed Job                                                                                      | Job pod logs                           |
| `bootstrap_backups.py`: `--apply` with placeholder name          | Guard trips                           | Fatal (hard error)                          | Non-zero exit, stderr message                                                                   | stderr                                 |
| `bootstrap_backups.py`: AWS error                                | Perms / already exists                | Partially — idempotent create-if-absent     | Non-zero exit, stderr                                                                           | stderr; `--dry-run` prints plan        |

## Input validation

- **`sync-secrets` JSON build:** extract the value map from the decrypted Secret
  with `yq -o=json '.stringData // (.data | map_values(@base64d))'` (handles both
  `stringData` and base64 `data`; excludes `apiVersion`/`kind`/`metadata`/`type`);
  the extracted map's keys must equal **exactly** `{DATABASE_URL, REDIS_URL}`;
  fail with a clear message naming the offending key (never its value) on a
  missing/extra key before building the JSON (prevents writing a malformed secret
  ESO would propagate). Type: YAML → flat JSON object. See "JSON extraction and
  the write→read contract".
- **`ExternalSecret.dataFrom.extract.key`:** fixed string per overlay, not user
  input.
- **`bootstrap_backups.py` args:** bucket name validated against S3 naming rules
  (lowercase, 3–63 chars, DNS-ish); region non-empty; **`--retention-days`
  defaults to `30`** (positive integer; documented adjustable) so the lifecycle
  rule and the `--dry-run` plan are deterministic; **`--apply` + a
  still-`PLACEHOLDER_` bucket/key/region → hard error**; `--dry-run` (default)
  against the placeholder prints the plan without error.
- **CronJob schedule:** static cron string; validated by the API server at apply
  time and schema-checked by kubeconform.

## Invariants and ownership

- **"No plaintext secret material in git"** — owned by
  `scripts/check_sops_encrypted.py` (unchanged; guards `deploy/**/secrets.*.yaml`).
  The new `ExternalSecret`/`ClusterSecretStore` filenames do **not** match
  `secrets.*`, so the guard ignores them — and they hold no secret material
  anyway. Confirmed against the guard's glob.
- **"`app-secrets` Secret shape (DATABASE_URL, REDIS_URL)" — the write→read
  contract (review finding 4).** The JSON written to Secrets Manager is a **flat
  object with exactly keys `DATABASE_URL`, `REDIS_URL`**; ESO's `dataFrom.extract`
  maps each JSON key to one `app-secrets` Secret key **1:1**; `envFrom: secretRef:
  app-secrets` consumes them in `deployment.yaml` and `migration-job.yaml`
  (unchanged). **The `sync-secrets` key-shape validation is the *single*
  load-bearing guard** for this contract: nothing validates it in-cluster at
  render time (kustomize/kubeconform never see values; `envFrom` does not require
  specific keys), so a malformed write would start pods **without**
  `DATABASE_URL`/`REDIS_URL` and crash them, invisible to the static gate. The
  write-side validator is therefore treated as mandatory, not cosmetic.
- **"Migrate before Deployment"** — owned by the Argo CD PreSync hook on the
  migration Job (was owned by `deploy.yml`, now deleted).
- **"ClusterSecretStore stays cluster-scoped (un-namespaced)"** — owned by its
  placement in the no-`namespace:` `deploy/argocd` kustomization; verified by the
  `k8s-check` render check.
- **"Template builds before init"** — owned by the `PLACEHOLDER_` convention; all
  new tokens are valid plain strings and registered in `placeholders.yml`.

---

## Known rough edges (DOCUMENT, do not engineer around)

1. **First-time bootstrap ordering.** Argo CD does not natively wait for the ESO
   sync (CRD health is not something Argo CD understands) the way it waits for
   the migration Job PreSync hook. On a brand-new environment the `app-secrets`
   Secret does not exist until ESO's first sync settles; the migration Job (and
   Deployment) may start before it and CrashLoop until the Secret appears. In
   steady state this never recurs (the Secret already exists from prior syncs).
   **Document in `docs/deployment.md`** as a one-time, manual-attention event: on
   a new env, let the `ExternalSecret` reach `SecretSynced` before triggering the
   first Argo CD sync (or re-sync once it does). **Do not** add sync-waves or
   health checks to engineer around it.
2. **Rotation does not restart running pods.** Env vars are read at container
   start, so updating a secret (SOPS → Secrets Manager → ESO refresh) updates the
   Kubernetes Secret but does not restart pods already running with the old
   values. **Document in `docs/secrets.md`.** Mention **Reloader** as a future
   option (watches Secrets, triggers a rolling restart) — **do not add it.**

---

## Testability

- **Static / CI-gated (no cluster):**
  - `uv run poe k8s-check` — renders dev/prod/argocd; kubeconform validates all
    (incl. the three CRDs via vendored schemas); kube-linter checks dev/prod
    (incl. the backup CronJob). Primary automated check for every new manifest.
  - `uv run poe docs-build` — `mkdocs build --strict` validates edited docs.
  - `scripts/bootstrap_backups.py` — unit-testable: arg parsing, name/region
    validation, the placeholder-guard, and the `--dry-run` plan are pure/mockable.
    Add `tests/unit/test_bootstrap_backups.py` (unit, no AWS; mock `boto3` or use
    `moto`). The `--dry-run` plan is deterministic because `--retention-days`
    defaults to a concrete `30`, so the test can assert the printed lifecycle
    expiry exactly. `--apply` AWS calls are integration-only, not run in CI.
  - `sync-secrets` key-validation: kept inline shell + `yq` (matches the former
    `deploy.yml` style); the PR-time dry-run decrypt catches breakage. Extract a
    tiny Python helper only if the shell grows complex.
  - `rg PLACEHOLDER_` — every new token present and valid; after a simulated init
    none remain.
  - GitHub workflow YAML — `actionlint` if available (SPEC §15) + pre-commit
    `check-yaml`.
- **Integration (needs a cluster — out of scope for template CI):** Argo CD sync,
  ESO materialization, and CronJob execution are validated in a real cluster by
  the consuming team, documented in `docs/deployment.md`. The template does not
  stand up a kind/k3d cluster (consistent with the Datadog Agent being assumed,
  not installed).

---

## Verification gates (run before declaring done)

1. `uv run poe k8s-check` — renders dev/prod/argocd; runs kubeconform **twice**
   (pass (a): lenient, with `-ignore-missing-schemas` backstop; pass (b): scoped
   to the CRD-bearing renders with `-ignore-missing-schemas` removed and
   `-schema-location` pointing only at `deploy/k8s/crd-schemas/`, so an
   unresolved `Application`/`ExternalSecret`/`ClusterSecretStore` schema fails the
   build — review finding 2); kube-linter clean on dev/prod; `deploy/argocd`
   renders the `ClusterSecretStore` with **no `metadata.namespace`**. The
   "resolves, not skips" guarantee is now a permanent part of the committed task,
   not a one-off run to be reverted.
2. `uv run poe docs-build` — `mkdocs build --strict` passes.
3. `uv run poe check` — lint + typecheck (incl. `bootstrap_backups.py` under
   `mypy --strict`) + test (incl. the new unit test) pass.
4. `uv run poe security` — gitleaks, ruff `S`, and the SOPS guard still pass (new
   manifests hold no secret material).
5. `rg PLACEHOLDER_` — lists exactly the expected tokens (old + new), all valid
   strings; nothing fabricated.
6. GitHub workflow YAML valid for both `ci.yml` and the new `sync-secrets.yml`
   (`actionlint` if present; pre-commit `check-yaml`).
7. Spot-checks: `deployment.yaml` + `migration-job.yaml` still reference
   `secretRef: app-secrets`; the migration Job carries the PreSync-hook
   annotations and its comment describes the hook lifecycle; overlay
   kustomizations list `externalsecret.yaml` and the old SOPS caveat blocks are
   gone; `.github/workflows/deploy.yml` is deleted; the `backup` SA has
   `automountServiceAccountToken: true`; the NetworkPolicy any-IP egress rule
   includes `443/TCP`.

---

## Out of scope (explicit)

- Installing Argo CD or ESO (assumed cluster-level, like the Datadog Agent).
- A Secrets Manager CMK (the default key is intentional; only backups get a CMK).
- Reloader / automatic pod restart on rotation (documented as a future option).
- Engineering around the first-boot ESO-vs-migration ordering (documented only).
- Point-in-time DB restore tooling (the CronJob does `pg_dump` backups; restore
  is a documented manual `psql`/`pg_restore` procedure, not automated here).
- Ingress/TLS, auth, and the remaining SPEC §13 open questions.
- Standing up a kind/k3d cluster in CI for integration tests.

---

## Responses to the design review

### This round (CHANGES_REQUESTED — 0 HIGH, 4 MEDIUM, 2 NIT)

All four MEDIUM findings are resolved and both NITs are addressed. Each was a
pre-decided resolution (the option was chosen by the orchestrator); the design
now reflects them.

1. **MEDIUM (finding 1) — ESO `external-secrets.io/v1` apiVersion asserted but no
   version floor.** Resolved by **keeping `v1`** and documenting a **hard minimum
   ESO version floor of v0.10.0** (when `v1` was promoted). The floor is stated in
   the "Locked technology stack" section **and** called out as an edit to
   `docs/deployment.md` next to the "ESO assumed pre-installed" note. The design
   makes the three things that must agree explicit: the vendored CRD schema
   filenames (`externalsecret_v1.json`, `clustersecretstore_v1.json`), the
   manifest `apiVersion` (`external-secrets.io/v1`), and the documented v0.10.0
   floor. Dropping below `v1` is flagged as a stack change.

2. **MEDIUM (finding 2) — "resolves, not skips" was a one-off manual run, not a
   durable CI gate.** Resolved by making it **durable** in the committed
   `poe k8s-check` task. kubeconform now runs **twice**: pass (a) is the existing
   lenient pass with `-ignore-missing-schemas` as the backstop for other CRDs;
   pass (b) is a **second scoped pass** over only the CRD-bearing renders
   (`argocd`, `dev`, `prod`) with `-ignore-missing-schemas` **removed** and
   `-schema-location` pointing **only** at `deploy/k8s/crd-schemas/` (core kinds
   excluded via `-skip`). A missing/mis-named vendored schema for `Application`,
   `ExternalSecret`, or `ClusterSecretStore` now **fails the build every run**.
   The exact mechanism is stated in the Quality-gate shell block, the
   `pyproject.toml` modify row, and Verification gate #1; the former
   "one-off run, then revert" language is removed.

3. **MEDIUM (finding 3) — backup pod NetworkPolicy label unstated.** Resolved.
   The backup CronJob spec now **requires** the pod template `metadata.labels` to
   include `app.kubernetes.io/name: k8s-fastapi-starter` (the entire basis for the
   `app` NetworkPolicy selecting the pod; without it egress to Postgres/S3/STS/
   KMS/Secrets Manager is denied and backups silently fail). The **full label
   set** the backup pod carries is stated:
   `app.kubernetes.io/name: k8s-fastapi-starter` (load-bearing) +
   `app.kubernetes.io/component: backup` (identity only). The `networkpolicy.yaml`
   modify row gains a one-line note that any future tightening of the any-IP
   egress to a `to:` selector must preserve the backup pod's egress to Postgres,
   S3, STS, KMS, and Secrets Manager.

4. **MEDIUM (finding 4) — kube-linter result on the backup CronJob/SA asserted
   clean without verification.** Resolved. The design **no longer pre-commits to
   "no exclusion needed."** During implementation the implementer must run
   kube-linter (**pinned 0.8.3, empty `exclude: []`**) against the rendered
   dev/prod overlays **with the CronJob present** and confirm zero new diagnostics
   **before** asserting clean; if a default check fires (likeliest
   `default-service-account` / SA-token-automount on
   `automountServiceAccountToken: true`), add the exclusion to
   `deploy/k8s/.kube-linter.yaml` **with a written reason per SPEC §14.9 in the
   same change**. This is reflected in the backup-SA paragraph, the Quality-gate
   kube-linter prose, and the `.kube-linter.yaml` modify row.

5. **NIT (finding 5) — `yq` flavor in the sync-secrets runner unspecified.**
   Resolved. The sync-secrets step list and the "JSON extraction" subsection now
   state `yq` is the **mikefarah build** (preinstalled on `ubuntu-latest`; pin a
   binary like the sops step if reproducibility is wanted) and that the
   **`@base64d` operator requires that flavor** (the Python `yq` lacks it).

6. **NIT (finding 6) — backup retention left unspecified.** Resolved. The
   bootstrap script CLI now defaults to **`--retention-days 30`** (documented as
   adjustable), so the lifecycle rule and the `--dry-run` plan are **deterministic**
   for the `--dry-run` unit test. Reflected in the bootstrap-script section, the
   Input-validation bullet, and the Testability note.

### Prior round (1 HIGH, 4 MEDIUM, 3 NIT — carried forward, still reflected in the design body)

The previous round's findings were already resolved in the design body and remain
in force:

- **HIGH — ADR process conflict.** The `docs/adr/0001-technology-choices.md` row
  specifies the three-part in-place edit (update Decision, replace the "CI job"
  bullet, amend the self-contradictory "requires a new ADR" bullet) plus a dated
  reversal note; the "ADR process reconciliation" subsection explains it.
- **MEDIUM — put/create fallback + pending-deletion edge** pinned
  (`put-secret-value` first, `create-secret` only on `ResourceNotFoundException`,
  no pre-probe; `InvalidRequestException` → fatal with a `restore-secret` hint).
- **MEDIUM — JSON extraction / value-map shape** pinned via the "JSON extraction
  and the write→read contract" subsection.
- **MEDIUM — write→read key mapping** named as the single load-bearing guard in
  Invariants and ownership.
- **MEDIUM — SPEC §13 item 5** appended with the additional `secretsmanager:*`
  IAM perms (item 5 stays open, not marked Decided).
- **NITs** — schema-location group omission documented; prod Application omits
  `automated` entirely while retaining `CreateNamespace=true`; the mandatory
  per-env `BACKUP_PREFIX` patch is listed in both overlay rows.

### Earliest round (carried forward, still reflected in the design body)

An earlier review round (10 findings) remains in force: backup SA
`automountServiceAccountToken: true` for IRSA; `sync-secrets` as a standalone
path-filtered workflow (not a `ci.yml` job); `ClusterSecretStore` placed in the
no-`namespace:` `deploy/argocd/` kustomization; vendored CRD schemas instead of a
remote catalog; `443/TCP` on the any-IP egress rule; the `ExternalSecret` named
`app-secrets-external` with `target.name: app-secrets`; the migration-Job comment
rewritten to the PreSync-hook lifecycle; `deploy.yml` deleted with break-glass
documented; the `secrets.md` transient break-glass footgun noted; and the
`fill`-vs-`external` placeholder split.
