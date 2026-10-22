# Security

Each gate below blocks merge. Pre-commit runs the fast ones locally, and CI runs all of them.

| Gate | Tool | Pre-commit | CI | Local command |
|---|---|---|---|---|
| Format | Ruff format | yes | yes | `uv run poe lint` |
| Lint | Ruff check | yes | yes | `uv run poe lint` |
| Typing | mypy --strict | yes | yes | `uv run poe typecheck` |
| Python dependency vulns | pip-audit | no | yes (`audit`) | `uv run poe audit` |
| Python code security | Ruff `S` rules | yes (via Ruff) | yes | `uv run poe security` |
| Secrets in git | gitleaks | yes | yes (full history weekly) | `uv run poe security` |
| Plaintext secret files | SOPS guard | yes | yes | `uv run poe security` |
| Dockerfile lint | hadolint | yes | yes | pre-commit |
| K8s schema | kubeconform | no | yes | `uv run poe k8s-check` |
| K8s security | kube-linter | no | yes | `uv run poe k8s-check` |
| Image vulns | Trivy (CRITICAL/HIGH, unfixed ignored) | no | yes, and weekly on `main` | CI |
| Commit message | conventional-pre-commit | yes (commit-msg) | yes (`commits` job, PRs) | `uv run poe hooks` |

## Dependency audit

`uv run poe audit` exports the exact lockfile versions with `uv export --frozen` and audits that list with
pip-audit against OSV. `--strict` fails on any vulnerability, including ones with no fix available.
Auditing the lockfile avoids drift from whatever happens to be installed locally.

## Python source

The Ruff `S` ruleset (bandit equivalents) is enabled. Test files ignore `S101` (assert). Each suppression is
per-line with a reason, for example `# noqa: S608 - static SQL, no user input`.

## Secrets

gitleaks scans the working tree in `uv run poe security` and full git history weekly. See [Secrets](secrets.md).

## Containers

- The image runs as a non-root user.
- Trivy fails the build on CRITICAL or HIGH findings that have a fix. Unfixed findings are ignored, and this is a
  deliberate trade-off. Review the scan output.
- Images are pushed to GHCR only from `main`, tagged with the commit SHA.

## Kubernetes

kubeconform validates against the Kubernetes schemas. kube-linter flags privileged containers, root users,
missing probes, and missing resource limits. Any kube-linter ignore needs a written reason in the same file.

## Dependency updates

Dependabot opens weekly update PRs for GitHub Actions, Python dependencies, the Docker base image, and
pre-commit hook revisions. Review them like any other change.

## Reporting a problem

Report suspected vulnerabilities to the owning team and to Security. Do not open a public issue with exploit details.
Do not paste real credentials into tickets or PRs. Rotate anything that was exposed.

## CI commit-message checks

`conventional-pre-commit` runs locally at the `commit-msg` stage. Because `pre-commit run --all-files` does not run
that stage, CI enforces the format separately: the **`commits`** job in `ci.yml` validates every commit in a pull
request with `conventional-pre-commit`. This is the backstop for contributors who skip the hook or use `--no-verify`,
and it keeps the git-cliff changelog grouping reliable.
