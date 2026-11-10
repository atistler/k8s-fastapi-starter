# Design Review (Confirming Round): Argo CD GitOps + SOPS → AWS Secrets Manager → ESO

Reviewed document: `.agents/tasks/k8s-fastapi-starter-feat-argocd-eso/design.md`
Review posture: fresh read, confirming that the six pre-decided resolutions from
the prior CHANGES_REQUESTED round (0 HIGH, 4 MEDIUM, 2 NIT) are genuinely applied
to the design text, and checking for any new HIGH/MEDIUM regressions.

## Scope note on the review target

The task referenced a worktree path (`.worktrees/argocd-eso`) that does not
exist; the review was performed against the live files on `main` at the repo
root. Current-state claims in the design were re-checked against those live
files. This is not a design defect and does not affect the verdict.

---

## Resolution verification (the six required fixes)

### 1. ESO apiVersion `external-secrets.io/v1` + v0.10.0 floor — RESOLVED

The "Locked technology stack" section fixes `external-secrets.io/v1` for **both**
`ExternalSecret` and `ClusterSecretStore`, and states a hard **minimum ESO
version v0.10.0** with the rationale (the API group was promoted to `v1` in
v0.10.0; older ESO serves only `v1beta1` and rejects the manifests). The section
explicitly requires three things to agree: the manifest `apiVersion`
(`external-secrets.io/v1`), the vendored schema filenames (`externalsecret_v1.json`,
`clustersecretstore_v1.json`), and the documented v0.10.0 floor. The
`docs/deployment.md` rewrite entry (Files to create → Documentation) also carries
the instruction to state the v0.10.0 floor next to the "ESO assumed pre-installed"
note and reiterates that the doc floor must match the manifest apiVersion and the
`_v1.json` filenames. The `clustersecretstore.yaml` and both `externalsecret.yaml`
specs in "Files to create" use `apiVersion: external-secrets.io/v1`. All four
loci agree. Confirmed resolved.

### 2. `poe k8s-check` runs kubeconform twice — RESOLVED

The "Quality gate" shell block runs kubeconform **twice**:
- Pass (a): lenient, `-strict -summary -ignore-missing-schemas` with
  `-schema-location default` + the vendored `-schema-location` template, over
  dev/prod/argocd (backstop for other CRDs).
- Pass (b): scoped to the CRD-bearing renders (`argocd.yaml`, `dev.yaml`,
  `prod.yaml`) with `-ignore-missing-schemas` **removed**, `-schema-location`
  pointing **only** at `deploy/k8s/crd-schemas/`, and core kinds excluded via
  `-skip` so the only kinds pass (b) must resolve are `Application`,
  `ExternalSecret`, `ClusterSecretStore`. A missing/mis-named/version-drifted
  vendored schema for those three fails the build every run.

The exact mechanism is stated in the Quality-gate block, repeated in the
`pyproject.toml` modify row, and in Verification gate #1. The former "one-off
manual run, then revert" language is removed and explicitly replaced with a
durable committed gate. Confirmed resolved.

### 3. Backup CronJob pod label + NetworkPolicy note — RESOLVED

The backup-cronjob spec requires the pod template `metadata.labels` to include
`app.kubernetes.io/name: k8s-fastapi-starter`, states it is the entire basis for
the `app` NetworkPolicy selecting the pod (absent it, egress to
Postgres/S3/STS/KMS/Secrets Manager is denied and backups silently fail), and
states the **full label set**: `app.kubernetes.io/name: k8s-fastapi-starter`
(load-bearing) + `app.kubernetes.io/component: backup` (identity only), with an
explicit "must NOT be labeled by component alone." The `networkpolicy.yaml`
modify row carries the one-line note that any future tightening of the any-IP
egress to a `to:` selector must preserve the backup pod's egress to Postgres
(5432), S3, STS, KMS, and Secrets Manager (443). The "Hard requirements"
callout box repeats the label requirement. Confirmed resolved.

Current-state check: `deploy/k8s/base/networkpolicy.yaml` on `main` has exactly
the two egress rules the design describes (a `kube-system`-scoped DNS rule, and a
second any-IP rule with 5432/6379/8126/8125), so the instruction to add 443 to
the second (any-IP) rule — not the DNS rule — is precise and correct.

### 4. kube-linter clean NOT pre-asserted — RESOLVED

The backup-SA paragraph, the Quality-gate kube-linter prose, and the
`.kube-linter.yaml` modify row all state the result is **verified, not assumed**:
run kube-linter pinned **0.8.3** with the repo's empty `exclude: []` against the
rendered dev/prod overlays **with the CronJob present**, confirm zero new
diagnostics before asserting clean, and if a default check fires (likeliest
`default-service-account` / SA-token-automount on `automountServiceAccountToken:
true`) add the exclusion to `deploy/k8s/.kube-linter.yaml` with a written SPEC
§14.9 reason in the same change. The design explicitly says "Do not pre-commit to
'no exclusion needed'." Confirmed resolved.

### 5. `yq` is mikefarah + `@base64d` requires that flavor — RESOLVED

The `sync-secrets` step list (step 3) states `yq` is the **mikefarah build**
(preinstalled on `ubuntu-latest`; pin a binary like the sops step if
reproducibility is wanted) and that the `@base64d` operator is mikefarah-specific
(the Python `yq` lacks it), so the flavor is load-bearing and Python `yq` must
not be substituted. The "JSON extraction and the write→read contract" subsection
and the "Input validation" bullet repeat this. Confirmed resolved.

### 6. Bootstrap `--retention-days 30` default — RESOLVED

The bootstrap-script section sets the lifecycle rule to expire objects after
`--retention-days` with a concrete default of **30**, documented as adjustable,
and ties the concrete default to a deterministic `--dry-run` plan that the unit
test asserts. The "Input validation" bullet and the "Testability" note both
restate the `30` default. Confirmed resolved.

---

## Internal-consistency and enumeration re-check

- The batch remains fully enumerated: Argo CD Applications (dev auto-sync, prod
  manual), ClusterSecretStore, per-overlay ExternalSecrets, the `sync-secrets`
  workflow (standalone, path-filtered, put-first/create-on-404, pending-deletion
  fatal case), vendored CRD schemas, the backup CronJob + backup SA + bootstrap
  script, the ADR 0001 in-place edit, the SPEC §1/§2/§3/§6/§9/§10/§13 edits, the
  `deploy.yml` deletion, the kustomization/networkpolicy/migration-job edits, the
  placeholder registry additions, and the doc rewrites. Files-to-create and
  Files-to-modify are mutually consistent (e.g., the `BACKUP_PREFIX` per-env
  patch is listed in both overlay rows and the base sets a neutral `base`
  prefix).
- The four MEDIUM resolutions are cross-referenced in three places each
  (narrative prose + the relevant Files table row + the Verification gates / per
  the "Responses to the design review" section), with no contradictory leftover
  language. The prior "one-off run then revert" wording for finding 2 is gone.
- No new HIGH or MEDIUM issue surfaced. kubeconform 0.8.0 (the pinned CI version)
  supports repeated `-schema-location`, the Go-template schema path, and `-skip`,
  so pass (b) is feasible as written.

## Verified Assumptions (checked against source)

1. `deploy/k8s/base/networkpolicy.yaml` on `main` has the two egress rules the
   design describes; the 443 addition targets the any-IP rule correctly.
2. The NetworkPolicy `podSelector` is `app.kubernetes.io/name:
   k8s-fastapi-starter`, confirming that the backup pod must carry that exact
   label to be selected — the load-bearing requirement finding 3 now states.

## Unverified / Wrong Assumptions

1. Vendored CRD schema file contents cannot be verified at design time (the files
   are created during implementation). The design's durable pass (b) gate is the
   correct mitigation and is now committed, so this is not a finding.
2. The consumer's installed ESO version is outside the repo's control; the design
   now documents the v0.10.0 floor rather than asserting it as fact, which is the
   appropriate resolution.

---

## Findings

No HIGH, MEDIUM, or NIT findings. All six prior findings are resolved in the
design text and no new issue surfaced.

## Verdict

HIGH findings: 0
MEDIUM findings: 0
NIT findings: 0

Per the mechanical rule (any HIGH or MEDIUM ⇒ CHANGES_REQUESTED; only NITs or
none ⇒ APPROVED), the verdict is **APPROVED**. The design is internally
consistent, fully enumerated, and every current-state claim spot-checked against
the live repo matched.
