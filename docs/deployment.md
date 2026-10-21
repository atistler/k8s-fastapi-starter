# Deployment

Deployment is **GitOps via Argo CD**, not a CI apply job. Argo CD applies the rendered Kustomize overlays; the
`sync-secrets` CI workflow and the External Secrets Operator (ESO) handle credentials. See SPEC.md section 10 and
[ADR 0001](adr/0001-technology-choices.md).

Argo CD and ESO are **assumed pre-installed, cluster-level**, like the Datadog Agent — this repo does not install
either.

> **ESO version floor: v0.10.0.** The `ExternalSecret`/`ClusterSecretStore` manifests use `apiVersion:
> external-secrets.io/v1`, and ESO only promoted that API group from `v1beta1` to `v1` in **v0.10.0**. A cluster on an
> older ESO serves only `v1beta1` and rejects these manifests (`no matches for kind "ExternalSecret" in version
> "external-secrets.io/v1"`). Upgrade ESO to at least v0.10.0 first. This floor is locked: the manifest `apiVersion`,
> the vendored schema filenames (`ExternalSecret_v1.json`, `ClusterSecretStore_v1.json`), and this note must agree.

## Layout

```text
deploy/
  argocd/                  Application (dev + prod) and the ESO ClusterSecretStore (cluster-scoped; no namespace)
  k8s/
    base/                  Deployment, Service, ConfigMap, ServiceAccount, NetworkPolicy, migration Job (PreSync hook),
                           backup CronJob, backup ServiceAccount
    crd-schemas/           vendored CRD JSON schemas for offline kubeconform validation
    overlays/dev/          kustomization.yaml, externalsecret.yaml, secrets.example.yaml
    overlays/prod/         kustomization.yaml, pdb.yaml, externalsecret.yaml, secrets.example.yaml
```

Render an overlay locally to check it:

```bash
kubectl kustomize deploy/k8s/overlays/dev
kubectl kustomize deploy/k8s/overlays/prod
kubectl kustomize deploy/argocd
uv run poe k8s-check   # all three renders, kubeconform (incl. vendored CRD schemas), kube-linter
```

The overlay no longer references a decrypted secrets file. The `app-secrets` Secret is materialized by ESO from the
overlay's `externalsecret.yaml`; nothing in the overlay carries secret material.

## Argo CD Applications

`deploy/argocd/` is a cluster-scoped, register-once-per-cluster Kustomization (no `namespace:` transformer, so the
`ClusterSecretStore` renders un-namespaced). It holds:

- **`application-dev.yaml`** — an Argo CD `Application` pointing at `deploy/k8s/overlays/dev`, destination namespace
  `fastapi-starter-dev`. `syncPolicy.automated` (prune + self-heal) with `CreateNamespace=true`.
- **`application-prod.yaml`** — same shape pointing at `overlays/prod`, destination `fastapi-starter-prod`, but it
  **omits `syncPolicy.automated` entirely**: no auto-sync, no auto-prune, no self-heal. Prod is synced **manually after
  review**, mirroring the "required reviewers for prod" posture. `CreateNamespace=true` is retained.
- **`clustersecretstore.yaml`** — the ESO `ClusterSecretStore` `aws-secrets-manager` naming the AWS Secrets Manager
  provider and region. It holds **no credentials**; ESO authenticates with its controller ServiceAccount's read-only
  IRSA role (configured at the cluster level, like the Datadog Agent).

`repoURL`, `spec.project`, the `argocd` install namespace, and the destination namespaces are placeholders a consumer
adjusts when registering each `Application` in their platform's Argo CD instance.

## How a sync works

1. A change lands on `main` (manifests and/or a `secrets.*.yaml` file).
2. If a `secrets.*.yaml` changed, the `sync-secrets` workflow pushes it to AWS Secrets Manager (below). ESO picks up the
   new value on its next refresh and updates the `app-secrets` Secret.
3. Argo CD syncs the `Application`: it renders the overlay and applies it. The **migration Job runs first** as a
   **PreSync hook** (`argocd.argoproj.io/hook: PreSync`, `sync-wave: "-1"`, `hook-delete-policy: BeforeHookCreation`) —
   Argo CD waits for it to complete before rolling out the Deployment. `BeforeHookCreation` deletes the prior hook Job
   before each sync, so the old commit-SHA-append naming dance is gone; the overlays still rename the Job per
   environment (`app-migrate-dev` / `app-migrate-prod`).

## Secrets: `sync-secrets` workflow

`.github/workflows/sync-secrets.yml` is a standalone, path-filtered workflow (triggers only on
`deploy/k8s/overlays/**/secrets.*.yaml` changes). On push to `main` it:

1. Assumes the AWS OIDC role (`vars.AWS_DEPLOY_ROLE_ARN` in `vars.AWS_REGION`). No long-lived keys.
2. Decrypts `secrets.<env>.yaml` with SOPS and extracts the value map.
3. Validates the key set is exactly `{DATABASE_URL, REDIS_URL}` (failing with the offending key name, never its value).
4. Writes one JSON entry `k8s-fastapi-starter/<env>/app-secrets` to AWS Secrets Manager (`put-secret-value`, falling
   back to `create-secret` only on `ResourceNotFoundException`).

Fork PRs run only the decrypt + key-shape check with no AWS access. The template ships only `secrets.example.yaml`, so
the workflow is a no-op until a real `secrets.<env>.yaml` is committed.

The OIDC role needs `secretsmanager:PutSecretValue`, `secretsmanager:CreateSecret`, and `secretsmanager:DescribeSecret`
scoped to `arn:aws:secretsmanager:*:*:secret:k8s-fastapi-starter/*/app-secrets-*`, on top of the `kms:Decrypt` it
already has for SOPS. The **default** Secrets Manager key is intentional: write-only (CI) and read-only (ESO) IAM roles
give the isolation a CMK would, so no secrets CMK is provisioned — see [Secrets](secrets.md).

## Database backups

`deploy/k8s/base/backup-cronjob.yaml` is a daily (`0 3 * * *` UTC) CronJob that streams `pg_dump "$DATABASE_URL" | gzip`
to the S3 backup bucket with SSE-KMS. It runs as the IRSA `backup` ServiceAccount
(`deploy/k8s/base/backup-serviceaccount.yaml`, `automountServiceAccountToken: true` so the AWS CLI can
`AssumeRoleWithWebIdentity`). The pod carries `app.kubernetes.io/name: k8s-fastapi-starter` so the `app` NetworkPolicy
selects it and permits egress to Postgres (5432) and the AWS endpoints (443). Each overlay patches `BACKUP_PREFIX` to
its environment (`dev` / `prod`) so dumps never collide under one bucket.

The backup image (`PLACEHOLDER_BACKUP_IMAGE`) is **not** the app image — it must carry the Postgres 17 client
(`pg_dump`) and AWS CLI v2. Supply or mirror it and **pin it by digest**.

Provision the bucket and the backup-only KMS CMK once per account with `scripts/bootstrap_backups.py`:

```bash
uv run python scripts/bootstrap_backups.py                 # dry-run (default); prints the plan incl. a 30-day expiry
uv run python scripts/bootstrap_backups.py --apply         # mutate AWS (fails if bucket/key/region are still PLACEHOLDER_)
# --retention-days N adjusts the S3 lifecycle expiry (default 30)
```

It is idempotent (create-if-absent for the bucket, CMK, and alias; versioning, public-access block, default SSE-KMS,
and a lifecycle expiry). It is not wired into `poe check` — it touches live AWS. The kube-linter config for `k8s-check`
is read via `--config deploy/k8s/.kube-linter.yaml`, which carries the two backup-related exclusions (the untagged
`PLACEHOLDER_BACKUP_IMAGE` FILL token, and the CronJob's TTL-vs-history-limit interaction) with written reasons.

## Break-glass manual apply

With the `deploy.yml` workflow gone, break-glass is an operator applying directly against a cluster they can reach:

```bash
kubectl apply -k deploy/k8s/overlays/dev
```

Do **not** try to break-glass a secret by `sops --decrypt … | kubectl apply -f -`: ESO's refresh will overwrite it.
Push the value to Secrets Manager (or edit the `ExternalSecret`) instead. See [Secrets](secrets.md).

## Rough edges (documented, not engineered around)

- **First-environment bootstrap ordering.** Argo CD does not understand ESO CRD health, so on a brand-new environment it
  will not "wait" for ESO to materialize `app-secrets` the way it waits for the migration Job PreSync hook. Let the first
  ESO sync settle before the first migration run. In steady state the Secret already exists from prior syncs, so this is
  a one-time, manual-attention event, not a recurring problem.
- **Rotation does not restart running pods.** Env vars are read at container start, so rotating a secret updates the
  `app-secrets` Secret but does not restart already-running pods. A controller such as Reloader would close that gap;
  it is a possible **future** addition and is not part of this template.

## Open items

- **Cluster access (SPEC section 13, item 3) — decided for the delivery model** (GitOps via Argo CD). Which specific
  cluster(s)/namespaces each `Application` targets and how it is registered remain per-platform.
- **Image tag selection.** Decide how the deployed image tag (and the matching `DD_VERSION` label) is set on the overlay
  before the first real deploy; until then it stays the placeholder `0.0.0`.
- **Ingress and TLS, authentication.** Open per SPEC section 13.
