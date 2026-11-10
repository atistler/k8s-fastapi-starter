# Expand PROJECT_SLUG rename coverage for Argo CD / ESO / backup files

The coder widened `PROJECT_SLUG.replace_in` in `.template/placeholders.yml` so the template-init rename now reaches every file where the literal `k8s-fastapi-starter` is a genuine project-identity target, and added two explanatory NOTE blocks (NetworkPolicy label coupling, and uv.lock regeneration). No literal `k8s-fastapi-starter` string was changed anywhere in the repo — only the registry metadata moved. The commit is scoped cleanly to one file and the buildable default (`default: k8s-fastapi-starter`) is preserved.

Watch for: the `git diff --stat HEAD~1` output lists three files, but that is a working-tree artifact, not the commit — the HEAD commit `e4487d0` contains only `.template/placeholders.yml` (confirmed). The two extra modified files (`deploy/k8s/base/configmap.yaml`, `docs/monitoring.md`) are pre-existing unstaged edits carried over from the prior workflow step and do not change any slug literal (slug counts unchanged 1→1 and 2→2). (confirmed)

**Verdict**: APPROVED

## High-level view

Coverage is complete. Cross-checking `rg -l --hidden -g '!.git' k8s-fastapi-starter` against the registry, every file that actually contains the literal is matched by either a `replace_in` glob or a `keep_as_is` entry (or the uv.lock NOTE). The genuine rename targets the task enumerated — `catalog-info.yml`, `deploy/argocd/**`, `deploy/k8s/**` (labels/selectors, DD service label, ExternalSecret `extract.key`, NetworkPolicy/PDB selectors, backup pod label), `sync-secrets.yml` Secrets Manager id, `pyproject.toml`, GHCR image paths, and the already-present `src`/`docs`/`tests` entries — are all present. The newly added targets (`.env.example`, `docs/deployment.md`, `docs/secrets.md`, `tests/unit/test_logging.py`) each contain the literal; every `replace_in` glob matches at least one real occurrence.

The two dropped entries are correct. `src/app/config.py` and `tests/unit/test_datadog.py` contain zero occurrences of the literal (verified with `rg -c`), so removing them removes stale paths, not coverage. The real `dd.service` assertion lives in `tests/unit/test_logging.py`, which is now listed.

Exclusions are right. `SPEC.md`, `README.md`, `AGENTS.md`, `CLAUDE.md`, and `CHANGELOG.md` are in `keep_as_is`, and `.template/**` is excluded — none were added to `replace_in`. `.claude/**` and `.agents/**` are kept as template/scratch. (CLAUDE.md and CHANGELOG.md happen to carry no live literal today, so their keep entries are defensive rather than strictly necessary, which is harmless.)

The pre-existing `deploy/argocd/**` and `.github/workflows/sync-secrets.yml` entries both survive the edit, and the NetworkPolicy label-coupling note is present and names both the app pods and the backup CronJob pod template as a set that must rename together.

<details>
<summary>Issues (0)</summary>

No blocking or actionable findings.

</details>

<details>
<summary>Details</summary>

### Commit scope is clean despite a noisy `diff --stat HEAD~1`

`git diff --stat HEAD~1` reports `.template/placeholders.yml`, `deploy/k8s/base/configmap.yaml`, and `docs/monitoring.md`. That command diffs the working tree against `HEAD~1`, so it folds in uncommitted edits. `git show --stat HEAD` confirms the actual commit touched only `.template/placeholders.yml` (32 insertions, 7 deletions), and `git status --porcelain` shows `configmap.yaml` and `monitoring.md` as unstaged ` M` entries left over from the prior step. Neither changes a slug literal: `git show HEAD:…` vs the working copy gives identical slug counts (configmap 1→1, monitoring 2→2). Criterion 5 (only `.template/placeholders.yml` in the commit, no PLACEHOLDER_ churn, default preserved) holds — the diff of the commit contains no `+`/`-` line touching a `PLACEHOLDER_` token, and `default: k8s-fastapi-starter` is intact. (confirmed)

### Coverage cross-check

The authoritative file set is `rg -l --hidden -g '!.git' k8s-fastapi-starter`. Mapping each slug-bearing file to the registry:

```
replace_in globs:
  pyproject.toml                         (1)   ✓
  catalog-info.yml                       (2)   ✓
  .github/workflows/sync-secrets.yml     (3)   ✓
  src/app/main.py                        (1)   ✓
  src/app/__init__.py                    (1)   ✓
  .env.example                           (1)   ✓   (added)
  docs/monitoring.md                     (2)   ✓
  docs/deployment.md                     (3)   ✓   (added)
  docs/secrets.md                        (1)   ✓   (added)
  tests/unit/test_logging.py             (2)   ✓
  deploy/k8s/**                          (13 files) ✓
  deploy/argocd/**                       (2 files)  ✓   (preserved)

keep_as_is / out-of-scope:
  SPEC.md, README.md, AGENTS.md          keep_as_is ✓
  .claude/skills/*/SKILL.md              .claude/**  ✓
  .agents/tasks/**                       .agents/**  ✓
  .template/placeholders.yml             .template/** ✓
  uv.lock                                NOTE (generated) ✓
```

The 13 `deploy/k8s/**` files include the base labels/selectors, `service.yaml`, `serviceaccount.yaml`, `configmap.yaml`, `networkpolicy.yaml`, `backup-serviceaccount.yaml`, `backup-cronjob.yaml`, both overlay `kustomization.yaml`, both `externalsecret.yaml` (`extract.key`), and `prod/pdb.yaml` — matching the task's enumerated targets (ExternalSecret extract.key, backup pod label, PDB selector, DD service label). No genuine rename target is missing.

### Dropped entries are stale, not lost coverage

The diff removes two `replace_in` entries: `src/app/config.py` and `tests/unit/test_datadog.py`. `rg -c k8s-fastapi-starter src/app/config.py` and `… tests/unit/test_datadog.py` both return 0 — these paths no longer carry the literal, so listing them was dead coverage. The DD service value asserted in tests is in `tests/unit/test_logging.py`, which remains listed. Removing stale paths also satisfies criterion 6 (no glob points at a file lacking the literal).

### NetworkPolicy label coupling note

The added NOTE states that `networkpolicy.yaml` selects pods by `app.kubernetes.io/name: k8s-fastapi-starter`, that the app pods (`deployment.yaml`/`migration-job.yaml`) and the backup CronJob pod template (`backup-cronjob.yaml`) both carry that label, and that the policy's own `podSelector`/`matchLabels` must change together or the backup pod silently loses egress. It explicitly instructs renaming every `app.kubernetes.io/name` under `deploy/k8s/**` in one pass. This satisfies criterion 4.

### Verification evidence

The audit records `uv run poe k8s-check` PASS (kubeconform 22/22, kube-linter clean), `uv run poe docs-build` PASS, and `rg PLACEHOLDER_` showing no new tokens. Per task instructions these suites were not re-run. The cheap cross-checks that were re-run (`rg` slug counts, `git diff --stat`, `git show --stat HEAD`, `git status`) all corroborate the audit's claims.

</details>

<details>
<summary>File map</summary>

- `.template/placeholders.yml` — expanded `PROJECT_SLUG.replace_in` (added `.env.example`, `docs/deployment.md`, `docs/secrets.md`, `tests/unit/test_logging.py`; dropped stale `src/app/config.py`, `tests/unit/test_datadog.py`), refined `deploy/**` comments, added NetworkPolicy-coupling and uv.lock NOTE blocks, added `CLAUDE.md`/`.claude/**`/`.agents/**` to `keep_as_is`.

Full commit: `git show e4487d0`.

</details>
