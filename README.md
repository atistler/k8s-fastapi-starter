# k8s-fastapi-starter

A template for a Kubernetes-deployed FastAPI service backed by PostgreSQL and Redis. A new service created from
it gets typed Python, versioned migrations, security gates, conventional-commit changelogs, and a Backstage
entry with TechDocs.

- **Specification (source of truth):** [SPEC.md](SPEC.md)
- **Documentation:** [docs/](docs/index.md). Build locally with `uv run poe docs`.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Docker with Compose. Python is pinned in `.python-version`, and uv installs it.

```bash
uv run poe install     # install dependencies
uv run poe up          # start Postgres 17 and Redis 7
uv run poe db-upgrade  # apply migrations
uv run poe run         # API on http://localhost:8000 (see /healthz and /readyz)
```

Run `uv run poe` to list every task. Before you open a PR, run `uv run poe hooks` once, then `uv run poe check-all`.

## Pre-commit hooks

Local quality gates run through [pre-commit](https://pre-commit.com/). They are the same checks CI runs, caught
before the commit is even made. Configuration is in [`.pre-commit-config.yaml`](.pre-commit-config.yaml); hook
versions are pinned to release tags and bumped by Dependabot (the `pre-commit` ecosystem).

Install the hooks once per clone (installs both the `pre-commit` and the `commit-msg` hook):

```bash
uv run poe hooks        # pre-commit install --install-hooks && pre-commit install --hook-type commit-msg
```

Then every `git commit` runs the hooks against the staged files. Run them against the whole tree at any time with
`uv run pre-commit run --all-files`; CI runs the same (`uvx pre-commit run --all-files --show-diff-on-failure`).

| Hook | Source | Stage | What it does |
|---|---|---|---|
| `trailing-whitespace`, `end-of-file-fixer` | pre-commit-hooks | commit | Normalize whitespace and final newlines. |
| `check-yaml` (`--allow-multiple-documents`) | pre-commit-hooks | commit | Parse YAML, including multi-document K8s manifests. |
| `check-toml` | pre-commit-hooks | commit | Parse `pyproject.toml` and other TOML. |
| `check-added-large-files` | pre-commit-hooks | commit | Block accidentally committed large blobs. |
| `detect-private-key` | pre-commit-hooks | commit | Block committed private keys. |
| `ruff-check` (`--fix`) | ruff-pre-commit | commit | Lint and auto-fix Python, including the `S` (security) rules. |
| `ruff-format` | ruff-pre-commit | commit | Format Python. |
| `gitleaks` | gitleaks | commit | Scan the staged changes for secrets. |
| `hadolint-docker` | hadolint | commit | Lint the `Dockerfile` (runs in Docker — needs a running daemon). |
| `conventional-pre-commit` | conventional-pre-commit | **commit-msg** | Enforce Conventional Commit messages (feeds the git-cliff changelog). |
| `check-sops-encrypted` | local (`scripts/check_sops_encrypted.py`) | commit | Fail if any `deploy/**/secrets.*.yaml` is not SOPS-encrypted. |
| `mypy` | local (`uv run mypy`) | commit | Type-check with `mypy --strict`. |

Notes:

- The `commit-msg` hook (`conventional-pre-commit`) only runs if you installed it — `uv run poe hooks` does. It
  rejects messages that do not follow `type(scope): subject`.
- `hadolint-docker` and the Docker-based checks need the Docker daemon running; the rest do not.
- The SOPS guard also runs with no arguments in `uv run poe security` and in CI, where it scans the whole repo.

## Continuous integration

CI/CD is GitHub Actions. Workflows live in [`.github/workflows/`](.github/workflows/) and call the same
`uv run poe` tasks you run locally, so local and CI behavior match. All workflows default to least privilege
(`permissions: contents: read`); only the jobs that publish images or releases widen it. Third-party actions are
pinned to commit SHAs (with the version in a trailing comment; Dependabot keeps them current), and uv is set up
with `astral-sh/setup-uv`, which reads `.python-version` so the Python version comes from one file.

### `ci.yml` — on every pull request and push to `main`

Jobs run in parallel; a push to `main` is cancelled and superseded by a newer one (`concurrency`,
`cancel-in-progress`).

| Job | Does | Gate |
|---|---|---|
| **lint** | `uv run poe install`, then `pre-commit run --all-files` | Ruff lint + format, the SOPS guard, and `mypy --strict` — all via pre-commit (no separate typecheck step, which would run mypy twice). |
| **commits** | Validates every commit in the PR with `conventional-pre-commit` | Conventional Commits (the git-cliff changelog depends on it); PRs only. |
| **test** | Spins up **Postgres 17** and **Redis 7** service containers (throwaway credentials), then `uv run poe install`, `uv run poe db-upgrade`, `uv run poe test` | Full pytest suite, including the `integration` tests, against real services. |
| **audit** | `uv run poe audit` | `pip-audit` against the exact locked versions exported from `uv.lock` (`--strict`). |
| **k8s** | Installs `kubeconform` + `kube-linter`, then `uv run poe k8s-check` | Renders the dev and prod overlays and validates schema + security of the manifests. |
| **docs** | `uv run poe docs-build` | `mkdocs build --strict` (TechDocs must build clean). |
| **image** | Builds the Docker image, **Trivy** scans it, and pushes to **GHCR** on `main` | Trivy fails on `CRITICAL`/`HIGH` with a fix available (`ignore-unfixed`). Push (tagged with the commit SHA) happens only on push to `main`, and that is the only job granted `packages: write`. |

### `release.yml` — on a `v*` tag

Builds and pushes the image to GHCR tagged with the version and `latest-release`, generates release notes with
**git-cliff** (`--latest --strip header`), and creates the GitHub release. Needs full history (`fetch-depth: 0`)
for git-cliff and is granted `contents: write` + `packages: write`. See [Releasing](docs/releasing.md).

### `security.yml` — weekly (Mondays) and on demand

Scheduled defense-in-depth beyond the per-PR gates: `pip-audit` on the locked dependencies, **gitleaks** over the
**full git history**, and a **Trivy** scan of the latest `main` image.

### `deploy.yml` — manual (`workflow_dispatch`)

Runs the SPEC §10 deploy sequence for a chosen environment (decrypt SOPS secrets in memory, apply the migration
Job and wait for it, then apply the overlay), using GitHub Environments for approvals and AWS OIDC (no stored
keys). **Not functional yet:** the cluster-access step fails on purpose until the deploy target is chosen
(SPEC §13, item 3). See [Deployment](docs/deployment.md).

The full gate matrix (what runs in pre-commit vs CI, and the `uv run poe` task behind each) is in
[docs/security.md](docs/security.md).

## Creating a real repository from this template

This repo is a starter template: identity values (project name, author, GitHub org, Backstage owner, AWS/KMS
settings, …) are **placeholders**. Every value that must be supplied is a `PLACEHOLDER_<NAME>` token — list
them all with `rg PLACEHOLDER_`. The machine-readable registry of every placeholder (fill / rename / keep /
runtime / external) is [`.template/placeholders.yml`](.template/placeholders.yml).

The fastest path is to ask an AI agent to "initialize this template" — [`AGENTS.md`](AGENTS.md) tells it exactly
how (read the registry, prompt you for each value, replace, verify). To do it by hand:

1. Create a repository from this template.
2. Open `.template/placeholders.yml` and gather a value for each `fill` and `rename` entry. `PROJECT_SLUG` (default
   `k8s-fastapi-starter`) must be a valid DNS-1123 label and Python distribution name.
3. Replace every `PLACEHOLDER_*` token and rename the slug where the registry says (not in `SPEC.md`/`README.md`/
   `AGENTS.md`). Set real secrets via `sops`, never in plaintext — see [Secrets](docs/secrets.md). Set the AWS
   deploy role/region as GitHub Environment variables — see [Deployment](docs/deployment.md).
4. Delete `.template/` and this "Creating a real repository…" section.
5. Run `uv run poe check`, confirm `rg PLACEHOLDER_` is empty, and decide the open items in SPEC.md section 13
   before the first deploy.

## Layout

See SPEC.md section 3 for the full tree. The main directories are `src/` (app), `migrations/` (Alembic),
`deploy/k8s/` (Kustomize), `docs/` (TechDocs), and `.github/workflows/` (CI, release, security, deploy).
