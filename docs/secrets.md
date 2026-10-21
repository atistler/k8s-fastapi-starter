# Secrets

Secrets are encrypted with **SOPS** and an **AWS KMS** key group, and SOPS stays the **git source of truth**. From
there the value flows to the cluster through AWS Secrets Manager and the External Secrets Operator (ESO) — Argo CD never
touches ciphertext.

## How it works

The chain is: **SOPS (git) → CI `sync-secrets` → AWS Secrets Manager → ESO → the `app-secrets` Kubernetes Secret.**

- `.sops.yaml` holds the creation rules. Rules are evaluated in order and the first match wins.
  - `secrets\.dev\.yaml$` and `secrets\.prod\.yaml$` use an AWS KMS key group. The ARN in the template is a placeholder.
  - `unencrypted_regex: '^(username|description)$'` keeps non-sensitive keys readable in diffs.
- Only `deploy/k8s/overlays/*/secrets.*.yaml` is a secret file that may be committed, and only encrypted.
- `scripts/check_sops_encrypted.py` runs in pre-commit, in `uv run poe security`, and in CI. It fails if any
  `deploy/**/secrets.*.yaml` file lacks a top-level `sops:` block.
- Plaintext examples live in `deploy/k8s/overlays/*/secrets.example.yaml`. They use placeholders only, are not
  matched by the guard, and are never applied.
- The **`sync-secrets` CI workflow** (`.github/workflows/sync-secrets.yml`), triggered when a `secrets.*.yaml` file
  changes on `main`, decrypts it and writes one JSON entry `k8s-fastapi-starter/<env>/app-secrets` (keys
  `DATABASE_URL`, `REDIS_URL`) to AWS Secrets Manager. It validates the key set is exactly `{DATABASE_URL, REDIS_URL}`
  and never logs values.
- The per-overlay **`ExternalSecret`** (`deploy/k8s/overlays/<env>/externalsecret.yaml`, named `app-secrets-external`,
  `target.name: app-secrets`) points ESO at that Secrets Manager entry through the cluster-scoped
  `ClusterSecretStore` `aws-secrets-manager`. **ESO** materializes the `app-secrets` Kubernetes Secret that the
  Deployment and migration Job consume by `secretRef`. The `ExternalSecret` carries zero secret material — it is just a
  pointer.
- **Default Secrets Manager key, no secrets CMK.** The isolation a CMK key policy would give is already provided by two
  IAM roles: the CI OIDC role is write-only (`PutSecretValue`/`CreateSecret`/`DescribeSecret`) and ESO's IRSA role is
  read-only (`GetSecretValue`/`DescribeSecret`). The only KMS CMK this template provisions is for the S3 backup bucket.

## Edit a secret

```bash
sops deploy/k8s/overlays/dev/secrets.dev.yaml   # opens the decrypted file in $EDITOR and re-encrypts on save
```

You need KMS decrypt and encrypt permission on the key group. Ask your team lead or Security for access.

## Apply (rotate) a secret

Rotation is just an edit and a commit — no manual AWS call for the normal case:

```bash
sops deploy/k8s/overlays/dev/secrets.dev.yaml   # edit, save (re-encrypts)
git commit -am "chore(secrets): rotate dev credentials"
# push to main -> sync-secrets writes it to Secrets Manager -> ESO syncs app-secrets into the cluster
```

> **Rotation does not restart running pods.** Env vars are read at container start, so the new value reaches new pods
> but not already-running ones. A controller such as Reloader would close that gap; it is a possible **future** addition
> and is not part of this template.

> **Break-glass footgun.** `sops --decrypt … | kubectl apply -f -` still works, but it is **transient**: ESO's
> `refreshInterval` (default 1h, and any reconcile) will overwrite the `app-secrets` Secret with the Secrets Manager
> value. Real break-glass is pushing the value to AWS Secrets Manager (or editing the `ExternalSecret`), not
> kubectl-applying a decrypted Secret.

Do not write decrypted output to disk. Pipe it.

## Rules

- **Never commit plaintext.** If gitleaks or the SOPS guard fires, fix the cause. Do not add an allowlist entry without
  agreement from the repository owner.
- **Use placeholders** in docs, examples, and tests. Example values in this repo are not real credentials.
- **Rotate** a credential by editing the encrypted file and committing; CI and ESO propagate it. Revoke the old value at
  its source afterwards.
- CI authenticates to AWS with OIDC, not stored keys. The role ARN is an environment variable.

## Open questions

The SOPS-vs-ESO question (SPEC section 13, item 4) is **decided** — ESO with AWS Secrets Manager, fed by SOPS via CI.
See [ADR 0001](adr/0001-technology-choices.md) and SPEC.md §9.1. The KMS key ARN and the CI role for decryption remain
open (items 5 and 3); item 5 now also covers the `secretsmanager:*` permissions the `sync-secrets` role needs.
