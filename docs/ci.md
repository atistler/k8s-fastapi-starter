# Continuous integration

CI/CD is GitHub Actions. Workflows live in `.github/workflows/` and call the same
`uv run poe` tasks you run locally, so local and CI behavior match. All workflows default to least privilege
(`permissions: contents: read`); only the jobs that publish images or releases widen it. Third-party actions are
pinned to commit SHAs (with the version in a trailing comment; Dependabot keeps them current), and uv is set up
with `astral-sh/setup-uv`, which reads `.python-version` so the Python version comes from one file.

## `ci.yml` — on every pull request and push to `main`

Jobs run in parallel; a push to `main` is cancelled and superseded by a newer one (`concurrency`,
`cancel-in-progress`).

| Job | Does | Gate |
|---|---|---|
| **lint** | `uv run poe install`, then `pre-commit run --all-files` | Ruff lint + format, the SOPS guard, and `mypy --strict` — all via pre-commit (no separate typecheck step, which would run mypy twice). |
| **commits** | Validates every commit in the PR with `conventional-pre-commit` | Conventional Commits (the git-cliff changelog depends on it); PRs only. |
| **test** | Spins up **Postgres 17** and **Redis 7** service containers (throwaway credentials), then `uv run poe install`, `uv run poe db-upgrade`, `uv run poe test` | Full pytest suite, including the `integration` tests, against real services. |
| **audit** | `uv run poe audit` | `pip-audit` against the exact locked versions exported from `uv.lock` (`--strict`). |
| **k8s** | Installs `kubeconform` + `kube-linter`, then `uv run poe k8s-check` | Renders dev, prod, and Argo CD resources; validates schemas and workload security. |
| **docs** | `uv run poe docs-build` | `mkdocs build --strict` (TechDocs must build clean). |
| **image** | Builds the Docker image, **Trivy** scans it, and pushes to **GHCR** on `main` | Trivy fails on `CRITICAL`/`HIGH` with a fix available (`ignore-unfixed`). Push (tagged with the commit SHA) happens only on push to `main`, and that is the only job granted `packages: write`. |

## `release.yml` — on a `v*` tag

Builds and pushes the image to GHCR tagged with the version and `latest-release`, generates release notes with
**git-cliff** (`--latest --strip header`), and creates the GitHub release. Needs full history (`fetch-depth: 0`)
for git-cliff and is granted `contents: write` + `packages: write`. See [Releasing](releasing.md).

## `security.yml` — weekly (Mondays) and on demand

Scheduled defense-in-depth beyond the per-PR gates: `pip-audit` on the locked dependencies, **gitleaks** over the
**full git history**, and a **Trivy** scan of the latest `main` image.

## Secrets synchronization and GitOps deployment

`sync-secrets.yml` decrypts changed SOPS secrets and syncs them to AWS Secrets Manager using AWS OIDC.
External Secrets Operator materializes the Kubernetes secrets, and Argo CD applies the environment overlays
with migrations as a PreSync hook. See [Deployment](deployment.md) and [Secrets](secrets.md).

The full gate matrix (what runs in pre-commit vs CI, and the `uv run poe` task behind each) is in
[Security](security.md).
