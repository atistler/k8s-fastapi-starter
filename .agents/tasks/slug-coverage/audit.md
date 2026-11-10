# PROJECT_SLUG coverage audit (`k8s-fastapi-starter`)

Template-init concern only. **No literal `k8s-fastapi-starter` string was changed
anywhere.** The only tracked file edited is `.template/placeholders.yml`
(`PROJECT_SLUG.replace_in` / `keep_as_is` and explanatory notes). The template
still builds and passes CI with its working default in place.

## Method

Enumerated every occurrence of the literal across the repo:

```
rg -n --hidden -g '!.git' k8s-fastapi-starter
```

Then classified each file as a **RENAME TARGET** (the slug is the new project's
identity and must be renamed at init) or **KEEP_AS_IS** (describes the template
itself, or is generated / scratch, and must not be renamed).

## Full occurrence list + classification

| File | Role of the literal | Class |
| --- | --- | --- |
| `pyproject.toml` | `[project].name` (Python dist name) | RENAME |
| `catalog-info.yml` | `metadata.name` + `github.com/project-slug` + GHCR image repo | RENAME |
| `deploy/k8s/base/deployment.yaml` | `app.kubernetes.io/name` labels/selector, `tags.datadoghq.com/service` label, GHCR image path | RENAME |
| `deploy/k8s/base/migration-job.yaml` | `app.kubernetes.io/name` label, GHCR image path | RENAME |
| `deploy/k8s/base/service.yaml` | `app.kubernetes.io/name` label + selector | RENAME |
| `deploy/k8s/base/serviceaccount.yaml` | `app.kubernetes.io/name` label | RENAME |
| `deploy/k8s/base/configmap.yaml` | `app.kubernetes.io/name` label | RENAME |
| `deploy/k8s/base/networkpolicy.yaml` | `metadata` label + **podSelector matchLabels** (load-bearing) | RENAME |
| `deploy/k8s/base/backup-serviceaccount.yaml` | `app.kubernetes.io/name` label | RENAME |
| `deploy/k8s/base/backup-cronjob.yaml` | `app.kubernetes.io/name` on the metadata + **pod template** (NetworkPolicy-selected) | RENAME |
| `deploy/k8s/overlays/dev/kustomization.yaml` | GHCR image name | RENAME |
| `deploy/k8s/overlays/prod/kustomization.yaml` | GHCR image name + `app.kubernetes.io/name` patch | RENAME |
| `deploy/k8s/overlays/dev/externalsecret.yaml` | Secrets Manager `extract.key` (`…/dev/app-secrets`) + comment | RENAME |
| `deploy/k8s/overlays/prod/externalsecret.yaml` | Secrets Manager `extract.key` (`…/prod/app-secrets`) + comment | RENAME |
| `deploy/k8s/overlays/prod/pdb.yaml` | `app.kubernetes.io/name` label + selector | RENAME |
| `deploy/argocd/application-dev.yaml` | Argo CD `Application` name (`…-dev`) + `source.repoURL` | RENAME |
| `deploy/argocd/application-prod.yaml` | Argo CD `Application` name (`…-prod`) + `source.repoURL` | RENAME |
| `.github/workflows/sync-secrets.yml` | Secrets Manager secret id `k8s-fastapi-starter/<env>/app-secrets` | RENAME |
| `src/app/main.py` | FastAPI `title=` | RENAME |
| `src/app/__init__.py` | package docstring | RENAME |
| `.env.example` | `DD_SERVICE` default | RENAME |
| `docs/monitoring.md` | `DD_SERVICE` example + `tags.datadoghq.com/service` example | RENAME |
| `docs/deployment.md` | Secrets Manager secret id + `app.kubernetes.io/name` NetworkPolicy refs | RENAME |
| `docs/secrets.md` | Secrets Manager secret id | RENAME |
| `tests/unit/test_logging.py` | `dd.service` sample value asserted in the log payload | RENAME |
| `SPEC.md` | describes the template | KEEP |
| `README.md` | describes the template | KEEP |
| `AGENTS.md` | describes the template | KEEP |
| `CLAUDE.md` | describes the template | KEEP |
| `CHANGELOG.md` | template history | KEEP |
| `.template/**` (`placeholders.yml`) | the registry itself | KEEP |
| `.claude/skills/*/SKILL.md` | agent-tooling prose ("this k8s-fastapi-starter service") | KEEP (see note) |
| `.agents/tasks/**` | task scratch / design journal, not shipped | KEEP |
| `uv.lock` | `[[package]] name` — **generated** from `pyproject.toml` | KEEP-but-regenerate (see note) |

Coverage was verified programmatically: every file containing the literal is
matched by either a `replace_in` or a `keep_as_is` pattern. The only file not
matched by either list is `uv.lock`, which is intentional — it is regenerated,
not hand-edited (note below).

## Exact `replace_in` changes made to `PROJECT_SLUG`

Added (new rename targets found in this audit):

- `.env.example` — `DD_SERVICE` default
- `docs/deployment.md` — Secrets Manager secret id + `app.kubernetes.io/name` refs
- `docs/secrets.md` — Secrets Manager secret id
- `tests/unit/test_logging.py` — `dd.service` sample value

Removed (stale paths that contained **no** slug literal — confirmed with
`rg -c`):

- `src/app/config.py` — no slug literal (dd_service is not configured here; it
  comes from the `DD_SERVICE` env/pod label)
- `tests/unit/test_datadog.py` — no slug literal (the real assertion lives in
  `tests/unit/test_logging.py`, now listed)

Preserved unchanged (per task): `deploy/argocd/**`,
`.github/workflows/sync-secrets.yml`, `deploy/k8s/**`, `pyproject.toml`,
`catalog-info.yml`, `src/app/main.py`, `src/app/__init__.py`,
`docs/monitoring.md`.

`keep_as_is` additions: `CLAUDE.md`, `.claude/**`, `.agents/**` (with notes).

## NetworkPolicy label-coupling note (added to placeholders.yml)

`deploy/k8s/base/networkpolicy.yaml` selects pods by
`app.kubernetes.io/name: k8s-fastapi-starter`. The application pods
(`deployment.yaml` / `migration-job.yaml`) **and** the backup CronJob pod
(`backup-cronjob.yaml` pod template) both carry that same label, so the policy
admits their egress. The policy's own `podSelector.matchLabels` must change
together with them. A rename MUST update every `app.kubernetes.io/name`
occurrence under `deploy/k8s/**` in one pass — renaming one without the others
makes the backup pod (or the app pods) silently lose egress. The `deploy/k8s/**`
glob covers all of them; the note exists so init renames them as a set rather
than piecemeal. This is recorded as a `NOTE` block in the `PROJECT_SLUG` entry.

## Ambiguous cases (recorded, not forced)

- `.claude/skills/*/SKILL.md` — the slug appears only in human-facing skill
  `description:` prose ("this k8s-fastapi-starter service"). Nothing
  load-bearing depends on it; it mirrors the `CLAUDE.md` / `AGENTS.md` tooling
  that is kept. Classified **KEEP_AS_IS** with a note; a downstream project may
  rename these by hand later, but template init should not.
- `uv.lock` — contains the slug but is generated from `pyproject.toml`. Added a
  note: after renaming `pyproject.toml`, run `uv lock` to regenerate; never
  hand-edit (per AGENTS.md).

## PROJECT_TITLE / PROJECT_DESCRIPTION

Re-checked. `PROJECT_TITLE` (`catalog-info.yml`) and `PROJECT_DESCRIPTION`
(`pyproject.toml`, `catalog-info.yml`) coverage is correct for the slug-adjacent
scope; no new human-facing title/description file was introduced by the Argo
CD/ESO/backup batch, so no extension was needed. Left unchanged to avoid
over-reach.

## Verification (run in repo root; local == CI)

- `uv run poe k8s-check` — **PASS** (kubeconform: 22/22 valid in both passes;
  kube-linter 0.8.3: no lint errors).
- `uv run poe docs-build` — **PASS** (`mkdocs build --strict` built the site;
  only the unrelated upstream MkDocs-2.0 advisory warning printed).
- `rg PLACEHOLDER_` — only the already-registered tokens appear. No new token
  was introduced. (`PLACEHOLDER_ARGOCD_NAMESPACE` appears **only** as prose in
  `.agents/.../design.md` explicitly stating that token was NOT created; it is
  not a live token in any shipped file.)
- Every `replace_in` glob matches ≥1 file containing the literal (no stale /
  typo'd paths): `pyproject.toml`(1), `catalog-info.yml`(1), `deploy/k8s/**`(13),
  `deploy/argocd/**`(2), `.github/workflows/sync-secrets.yml`(1),
  `src/app/main.py`(1), `src/app/__init__.py`(1), `.env.example`(1),
  `docs/monitoring.md`(1), `docs/deployment.md`(1), `docs/secrets.md`(1),
  `tests/unit/test_logging.py`(1).
- `git diff --stat` for the staged commit shows **only**
  `.template/placeholders.yml`. (Pre-existing, unrelated working-tree edits to
  `deploy/k8s/base/configmap.yaml` and `docs/monitoring.md` were present before
  this step from the prior workflow step; they were NOT touched here, NOT staged,
  and do not change any slug literal — the `monitoring.md` hunk is table
  reformatting with the literal present on both sides.)
