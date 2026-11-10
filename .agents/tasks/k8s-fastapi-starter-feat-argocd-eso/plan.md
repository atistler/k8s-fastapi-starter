# Implementation Plan — Argo CD GitOps + SOPS → AWS Secrets Manager → ESO + DB backups

Source of truth: `design.md` in this directory (read it — the Files-to-create /
Files-to-modify tables and the Quality-gate shell block are authoritative).
Work is DIRECTLY on branch `main` in the repo root
`/Users/adamtistler/PycharmProjects/k8s-fastapi-starter` — NO worktree. The repo
has an unborn HEAD; the first commit below becomes the repo's first commit. All
paths are absolute under that root. Local == CI via poe tasks (AGENTS.md).

Ordering rationale: vendored CRD schemas + the rewritten `k8s-check` gate come
first so every later manifest can be validated as it lands; the new manifests
and placeholders next; CI workflow + ADR/SPEC/docs prose last; the full
verification sweep and commits at the end. Each item leaves the tree buildable.

- [ ] 1. Vendor CRD JSON schemas and rewrite the `k8s-check` gate so the three new
      CRD kinds are *resolved, not skipped*, durably in CI.
      Create `deploy/k8s/crd-schemas/externalsecret_v1.json`,
      `deploy/k8s/crd-schemas/clustersecretstore_v1.json`,
      `deploy/k8s/crd-schemas/application_v1alpha1.json` (each the published
      OpenAPI/JSON schema for that CRD; filenames match kubeconform's
      `{{.ResourceKind|toLower}}_{{.ResourceAPIVersion}}.json` so one
      `-schema-location` dir resolves all three — group intentionally omitted),
      plus `deploy/k8s/crd-schemas/README.md` recording provenance, apiVersions,
      and the group-omission caveat. Rewrite the `[tool.poe.tasks.k8s-check]`
      shell in `pyproject.toml` to the two-pass form in design.md "Quality gate":
      render dev/prod/argocd; kubeconform pass (a) lenient
      (`-ignore-missing-schemas`, `-schema-location default` +
      `-schema-location deploy/k8s/crd-schemas/...`); kubeconform pass (b) scoped
      to the CRD-bearing renders with `-ignore-missing-schemas` REMOVED,
      `-schema-location` pointing ONLY at `deploy/k8s/crd-schemas/...`, core kinds
      excluded via `-skip`; kube-linter on dev/prod only. Add the one-line
      group-omission comment above the task.
      Files: `deploy/k8s/crd-schemas/externalsecret_v1.json`,
      `deploy/k8s/crd-schemas/clustersecretstore_v1.json`,
      `deploy/k8s/crd-schemas/application_v1alpha1.json`,
      `deploy/k8s/crd-schemas/README.md`, `pyproject.toml`.
      Verify: `uv run poe k8s-check` — at this point no CRD manifests exist yet,
      so pass (b) renders are schema-only; it must run without a shell/flag error
      (dev/prod still render and lint clean). Full CRD resolution is re-verified
      in step 9.

- [ ] 2. Add the Argo CD `deploy/argocd/` kustomization: two `Application` CRs and
      the `ClusterSecretStore`, cluster-scoped (NO `namespace:` transformer).
      Create `deploy/argocd/application-dev.yaml` (argoproj.io/v1alpha1
      Application, `metadata.namespace: argocd`, `spec.project: default`,
      `source.repoURL: https://github.com/PLACEHOLDER_GITHUB_ORG/k8s-fastapi-starter.git`,
      `source.path: deploy/k8s/overlays/dev`, `targetRevision: HEAD`,
      `destination.server: https://kubernetes.default.svc`,
      `destination.namespace: fastapi-starter-dev`, `syncPolicy.automated`
      {prune:true, selfHeal:true} + `syncOptions: [CreateNamespace=true]`),
      `deploy/argocd/application-prod.yaml` (same shape, path
      `deploy/k8s/overlays/prod`, namespace `fastapi-starter-prod`, but OMIT
      `automated` entirely — manual sync, no prune/selfHeal — while KEEPING
      `syncOptions: [CreateNamespace=true]`; document the diff inline),
      `deploy/argocd/clustersecretstore.yaml` (external-secrets.io/v1
      ClusterSecretStore `metadata.name: aws-secrets-manager`, no namespace,
      `spec.provider.aws: {service: SecretsManager, region: PLACEHOLDER_AWS_REGION}`,
      `auth` omitted — ESO controller IRSA; inline comment),
      `deploy/argocd/kustomization.yaml` (resources: the three files; NO
      `namespace:` field).
      Files: `deploy/argocd/application-dev.yaml`,
      `deploy/argocd/application-prod.yaml`,
      `deploy/argocd/clustersecretstore.yaml`,
      `deploy/argocd/kustomization.yaml`.
      Verify: `uv run poe k8s-check` — the argocd render now carries Application +
      ClusterSecretStore; both kubeconform passes resolve them against the
      vendored schemas; confirm `kubectl kustomize deploy/argocd` emits the
      ClusterSecretStore with NO `metadata.namespace`.

- [ ] 3. Add the per-overlay `ExternalSecret` CRs and remove the obsolete SOPS
      caveat comment blocks.
      Create `deploy/k8s/overlays/dev/externalsecret.yaml` and
      `deploy/k8s/overlays/prod/externalsecret.yaml` (external-secrets.io/v1
      ExternalSecret, `metadata.name: app-secrets-external`,
      `spec.refreshInterval: 1h`, `spec.secretStoreRef: {name: aws-secrets-manager,
      kind: ClusterSecretStore}`, `spec.target.name: app-secrets` +
      `creationPolicy: Owner`, `spec.dataFrom[0].extract.key:
      k8s-fastapi-starter/<env>/app-secrets`). In both overlay
      `kustomization.yaml` files: add `externalsecret.yaml` to `resources:`;
      delete the "BEFORE DEPLOY …" / "secrets.<env>.yaml is intentionally NOT
      listed" comment blocks and replace with a short note that `app-secrets` is
      materialized by ESO from `externalsecret.yaml` and rotation is sops →
      commit → CI → Secrets Manager → ESO.
      Files: `deploy/k8s/overlays/dev/externalsecret.yaml`,
      `deploy/k8s/overlays/prod/externalsecret.yaml`,
      `deploy/k8s/overlays/dev/kustomization.yaml`,
      `deploy/k8s/overlays/prod/kustomization.yaml`.
      Verify: `uv run poe k8s-check` — dev/prod renders carry a namespaced
      ExternalSecret (stamped `fastapi-starter-{dev,prod}`); both kubeconform
      passes resolve it; kube-linter clean.

- [ ] 4. Convert the migration Job to an Argo CD PreSync hook.
      In `deploy/k8s/base/migration-job.yaml` add
      `argocd.argoproj.io/hook: PreSync`,
      `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation`,
      `argocd.argoproj.io/sync-wave: "-1"` to `metadata.annotations`; rewrite the
      header comment to describe the PreSync-hook lifecycle (hook recreated each
      sync via BeforeHookCreation; the SHA-append dance is gone; overlays still
      rename the Job per env). Keep spec/security/resources otherwise unchanged.
      Files: `deploy/k8s/base/migration-job.yaml`.
      Verify: `uv run poe k8s-check` passes; confirm both overlay renders still
      show the Job renamed `app-migrate-{dev,prod}` and carrying the three
      annotations and `secretRef: app-secrets`.

- [ ] 5. Add the daily DB-backup CronJob + its IRSA ServiceAccount, wire into base,
      add the mandatory per-overlay `BACKUP_PREFIX` patch, and open 443 egress.
      Create `deploy/k8s/base/backup-serviceaccount.yaml` (SA `backup`,
      `eks.amazonaws.com/role-arn: PLACEHOLDER_BACKUP_ROLE_ARN`,
      `automountServiceAccountToken: true`) and
      `deploy/k8s/base/backup-cronjob.yaml` (batch/v1 CronJob `db-backup`,
      `schedule: "0 3 * * *"`, `concurrencyPolicy: Forbid`, history limits; pod
      template labels MUST include `app.kubernetes.io/name: k8s-fastapi-starter`
      [load-bearing for NetworkPolicy] AND `app.kubernetes.io/component: backup`;
      `serviceAccountName: backup`; pod+container securityContext mirroring the
      migration Job [runAsNonRoot, uid/gid 10001, seccomp RuntimeDefault,
      allowPrivilegeEscalation:false, readOnlyRootFilesystem:true, drop ALL];
      `restartPolicy: OnFailure`, backoffLimit, activeDeadlineSeconds,
      ttlSecondsAfterFinished, resource requests/limits; writable `/tmp`
      emptyDir; `image: PLACEHOLDER_BACKUP_IMAGE`; `BACKUP_PREFIX: base` container
      env; `DD_TRACE_ENABLED: "false"`; `envFrom: secretRef: app-secrets`;
      command streams `pg_dump "$DATABASE_URL" | gzip` to
      `aws s3 cp - s3://PLACEHOLDER_BACKUP_BUCKET/${BACKUP_PREFIX}/db/<ts>.sql.gz
      --sse aws:kms --sse-kms-key-id PLACEHOLDER_BACKUP_KMS_KEY_ID`). Add both to
      `deploy/k8s/base/kustomization.yaml` `resources:` and rewrite that file's
      header secrets-caveat comment (app-secrets is materialized by ESO from the
      overlay ExternalSecret). In each overlay `kustomization.yaml` add a
      MANDATORY patch setting the `db-backup` container env `BACKUP_PREFIX` to
      `dev` / `prod`. In `deploy/k8s/base/networkpolicy.yaml` add
      `- protocol: TCP; port: 443` to the existing any-IP (no-`to:`) egress rule
      with a reason comment (S3/STS/KMS/Secrets Manager for the backup pod) and a
      one-line note in the TIGHTEN comment that any future `to:` selector must
      preserve the backup pod's egress to 5432 + 443. Do NOT touch the DNS rule.
      Files: `deploy/k8s/base/backup-serviceaccount.yaml`,
      `deploy/k8s/base/backup-cronjob.yaml`,
      `deploy/k8s/base/kustomization.yaml`,
      `deploy/k8s/base/networkpolicy.yaml`,
      `deploy/k8s/overlays/dev/kustomization.yaml`,
      `deploy/k8s/overlays/prod/kustomization.yaml`.
      Verify: `uv run poe k8s-check` — run kube-linter (pinned 0.8.3, empty
      `exclude: []`) against dev/prod WITH the CronJob present and confirm zero
      new diagnostics; if a default check fires (likeliest
      `default-service-account`/token-automount on
      `automountServiceAccountToken: true`), add the exclusion to
      `deploy/k8s/.kube-linter.yaml` WITH a written reason (SPEC §14.9) in this
      same step and re-run. Confirm both overlay renders show `BACKUP_PREFIX`
      patched to the env name.

- [ ] 6. Add the backup bootstrap script and its unit test; add boto3 dev deps.
      Run `uv add --dev boto3 "boto3-stubs[s3,kms]"` (updates `pyproject.toml` +
      `uv.lock`; never hand-edit the lock). Create
      `scripts/bootstrap_backups.py` matching `check_sops_encrypted.py`
      conventions (module docstring, typed `mypy --strict`-clean funcs, argparse,
      `main(argv: list[str]) -> int`, stdlib + boto3): args `--bucket`
      (default literal `PLACEHOLDER_BACKUP_BUCKET`), `--kms-alias`/`--kms-key`
      (placeholder defaults), `--region`, `--retention-days` (default 30),
      `--dry-run` (DEFAULT) vs `--apply`; idempotent create-if-absent for bucket
      + CMK + alias, versioning, block-public-access, default SSE-KMS, lifecycle
      expiry at `--retention-days`; `--apply` with a still-`PLACEHOLDER_`
      bucket/key/region is a HARD error; S3 name + non-empty region validation;
      deterministic `--dry-run` plan output naming the 30-day expiry. Create
      `tests/unit/test_bootstrap_backups.py` (unit, no AWS; mock boto3 or moto)
      covering arg parsing, name/region validation, the placeholder guard, and
      the `--dry-run` plan asserting the 30-day expiry. Optionally add a
      `[tool.poe.tasks.backup-bootstrap]` convenience task (dry-run default); do
      NOT wire it into `check`/`check-all`.
      Files: `scripts/bootstrap_backups.py`,
      `tests/unit/test_bootstrap_backups.py`, `pyproject.toml`, `uv.lock`.
      Verify: `uv run poe check` — lint + `mypy --strict` + pytest all pass
      including the new unit test.

- [ ] 7. Replace the deploy workflow with the standalone `sync-secrets` workflow.
      Delete `.github/workflows/deploy.yml`. Create
      `.github/workflows/sync-secrets.yml` per design.md "CI sync-secrets
      workflow": workflow-level `on.push`/`on.pull_request` `paths:
      ['deploy/k8s/overlays/**/secrets.*.yaml']`; `permissions: contents: read`,
      job adds `id-token: write`; `strategy.matrix.env: [dev, prod]`; steps =
      checkout (same pinned SHA as ci.yml), skip-if-absent guard, install sops
      (reproduce deploy.yml's pinned-binary pattern), configure-aws-credentials
      (same pinned SHA) gated by
      `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`,
      then decrypt + extract with mikefarah `yq`
      (`sops -d … | yq -o=json '.stringData // (.data | map_values(@base64d))'`)
      + validate keys equal exactly `{DATABASE_URL, REDIS_URL}` (names only, no
      values) + write with put-first / create-on-`ResourceNotFoundException` /
      FATAL on `InvalidRequestException` (restore-secret hint), secret id
      `k8s-fastapi-starter/<env>/app-secrets`, plaintext in `$RUNNER_TEMP` only +
      masked. Add the IAM code comment above the job (role needs
      `secretsmanager:PutSecretValue`/`CreateSecret`/`DescribeSecret` — and
      RestoreSecret note — scoped to
      `arn:aws:secretsmanager:*:*:secret:k8s-fastapi-starter/*/app-secrets-*`, on
      top of existing `kms:Decrypt`). Pin actions to full SHAs reused from
      ci.yml/deploy.yml.
      Files: `.github/workflows/sync-secrets.yml` (new),
      `.github/workflows/deploy.yml` (deleted).
      Verify: `actionlint .github/workflows/sync-secrets.yml` if available, else
      `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/sync-secrets.yml'))"`;
      and `pre-commit run check-yaml --files .github/workflows/sync-secrets.yml`.

- [ ] 8. Register the new placeholders and update the registry references.
      In `.template/placeholders.yml`: add `fill` entries for
      `PLACEHOLDER_BACKUP_BUCKET`, `PLACEHOLDER_BACKUP_KMS_KEY_ID`,
      `PLACEHOLDER_BACKUP_ROLE_ARN`, `PLACEHOLDER_BACKUP_IMAGE` (with the files
      each appears in, per design.md token table); extend
      `PROJECT_SLUG.replace_in` with `deploy/argocd/**` and
      `.github/workflows/sync-secrets.yml`; change the `external`
      `AWS_DEPLOY_ROLE_ARN` and `AWS_REGION` `referenced_in` from
      `.github/workflows/deploy.yml` to `.github/workflows/sync-secrets.yml`.
      Files: `.template/placeholders.yml`.
      Verify: `rg PLACEHOLDER_` lists only intended tokens; every new
      `PLACEHOLDER_BACKUP_*` token appears both in a manifest/script AND in the
      registry; `uv run poe k8s-check` still renders (tokens are valid strings).

- [ ] 9. Update ADR 0001, SPEC.md, and docs prose for the GitOps + ESO + backup
      flow. ADR `docs/adr/0001-technology-choices.md` (keep "Accepted", do NOT
      add a new ADR): (a) Decision line adds Argo CD GitOps + SOPS→Secrets
      Manager→ESO; (b) replace the "Deployment is a CI job, not GitOps" bullet
      with the GitOps+ESO statement; (c) amend the "requires a new ADR" bullet to
      permit in-place/superseding updates; (d) append a dated reversal note (use
      today's date — do not fabricate). SPEC.md: §1 (GitOps adopted, drop from
      non-goals), §2 (add Argo CD row; change Secrets row), §3 (add new paths +
      infra to the tree/list), §6 (kubeconform CRD-schema note), §9 (SOPS→Secrets
      Manager→ESO flow + two-role isolation, backup-only CMK), §10 (Argo CD
      applies manifests + PreSync-hook ordering + new sync-secrets workflow;
      deploy.yml removed), §13 (mark Q3/Q4 "Decided —" pointing at ADR 0001;
      append the extra `secretsmanager:*` perms note to Q5, left open). Docs:
      rewrite `docs/deployment.md` (Argo CD; two Applications +
      ClusterSecretStore; sync flow; migration PreSync hook; sync-secrets;
      bootstrap_backups.py + backup CronJob + backup image pinned-by-digest note;
      break-glass `kubectl apply -k`; ESO v0.10.0 floor next to the
      assumed-preinstalled note; the two rough edges); rewrite `docs/secrets.md`
      (new chain; Q4 decided; rotation rough edge; transient break-glass footgun;
      Reloader mentioned as future option, NOT added); update
      `docs/architecture.md` (Argo CD + ESO + backup CronJob in the K8s shape).
      No `mkdocs.yml` nav change.
      Files: `docs/adr/0001-technology-choices.md`, `SPEC.md`,
      `docs/deployment.md`, `docs/secrets.md`, `docs/architecture.md`.
      Verify: `uv run poe docs-build` — `mkdocs build --strict` passes.

- [ ] 10. Full verification sweep (all gates green before committing).
      Run `uv run poe k8s-check` (dev+prod+argocd render; BOTH kubeconform passes
      incl. the no-ignore-missing CRD pass resolving Application/ExternalSecret/
      ClusterSecretStore; kube-linter clean; ClusterSecretStore un-namespaced),
      `uv run poe docs-build`, `uv run poe check` (lint + mypy --strict + tests),
      `uv run poe security` (gitleaks + ruff S + SOPS guard — new manifests hold
      no secret material), `rg PLACEHOLDER_` (only intended tokens), and validate
      `sync-secrets.yml` with `actionlint` if available else YAML-parse. Fix any
      failure before proceeding. Clean up any temp files.
      Files: none (verification only).
      Verify: every command above exits 0.

- [ ] 11. Commit the batch as Conventional Commits (stage specific files, NOT
      `git add -A`, NO `--no-verify`). Suggested grouping:
      `feat(deploy): adopt Argo CD GitOps with External Secrets Operator`
      (deploy/argocd/**, overlays' externalsecret + kustomization edits,
      migration-job hook, networkpolicy 443, crd-schemas/**, k8s-check rewrite,
      placeholders for GitOps);
      `ci(secrets): sync SOPS secrets to AWS Secrets Manager`
      (sync-secrets.yml add, deploy.yml delete, placeholders referenced_in);
      `feat(backup): daily database backup CronJob + bootstrap script`
      (backup-cronjob/serviceaccount, base kustomization, BACKUP_PREFIX patches,
      bootstrap_backups.py + test, boto3 deps, backup placeholders);
      `docs: document Argo CD + ESO secrets flow` (ADR 0001, SPEC.md, docs/**).
      This is the repo's first commit (unborn HEAD). Let pre-commit hooks run; on
      hook-driven changes, re-stage and make a NEW commit (no `--amend` after a
      hook failure).
      Files: all changed files, staged per group.
      Verify: `git log --oneline` shows the commits; `git status` clean; a final
      `uv run poe k8s-check && uv run poe docs-build` still passes.

## Notes / assumptions

- `actionlint` is not installed locally; the fallback is a YAML parse +
  pre-commit `check-yaml` (SPEC §15 says "actionlint if available").
- Today's date for the ADR reversal note must be the real implementation-time
  date; do not fabricate.
- The backup image, bucket, KMS key id, and role ARN stay `PLACEHOLDER_` tokens;
  the template must still build with them in place.
