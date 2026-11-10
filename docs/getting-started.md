# Getting started

## Prerequisites

| Tool | Used for | Install |
|---|---|---|
| uv | Python, dependencies, all Python tools | `brew install uv` |
| Docker (with Compose) | Local Postgres 17 and Redis 7, image builds, hadolint | Docker Desktop or Colima |
| pre-commit | Local quality gates | Installed by `uv run poe install` (dev group) |
| gitleaks | Secret scanning (`uv run poe security`) | `brew install gitleaks` |
| kubectl | Renders overlays (`uv run poe k8s-check`) | `brew install kubectl` |
| kubeconform, kube-linter | Manifest checks (`uv run poe k8s-check`) | `brew install kubeconform kube-linter` |
| git-cliff | Changelog (`uv run poe changelog`) | `brew install git-cliff` |
| SOPS | Secret editing (see [Secrets](secrets.md)) | `brew install sops` |

Python is pinned in `.python-version`. uv reads that file and installs the right interpreter.
You do not need to install Python yourself.

## Quick start

Run these commands from the repository root. Copy the example configuration before
starting the API or running migrations; its local credentials match Docker Compose.

```bash
uv run poe install     # uv sync --all-groups
cp .env.example .env   # local-only configuration; keep it out of git
uv run poe up          # start Postgres 17 and Redis 7 with Docker Compose
uv run poe db-upgrade  # apply Alembic migrations to DATABASE_URL
uv run poe run         # start the API with auto-reload on http://localhost:8000
```

Check it is up:

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/readyz
```

Run the tests and the full local gate set:

```bash
uv run poe test
uv run poe check-all
```

Install the git hooks once per clone:

```bash
uv run poe hooks
```

Use `uv run poe down` when you are finished. Compose keeps the Postgres data volume
so the next session can reuse it.

## If something fails

| Symptom | Check |
|---|---|
| Docker cannot connect | Start Docker Desktop or your container runtime, then retry `uv run poe up`. |
| Settings validation fails | Confirm `.env` exists and contains `DATABASE_URL` and `REDIS_URL`. |
| `/readyz` returns 503 | Check local services are running and the `.env` credentials match `docker-compose.yml`. |
| A table does not exist | Run `uv run poe db-upgrade` against the same database as the API. |

See [Configuration](configuration.md) for environment variables and
[Contributing](contributing.md) for hooks and the pull request workflow.

## Tasks

Defined in `pyproject.toml` (`[tool.poe.tasks]`); see SPEC.md §12 for the source of truth.

| Task | What it does |
|---|---|
| `uv run poe install` | Install all dependencies, including dev groups |
| `uv run poe run` | Run the API with reload |
| `uv run poe up` / `uv run poe down` | Start or stop local Postgres and Redis |
| `uv run poe lint` | Ruff lint and format check |
| `uv run poe format` | Apply Ruff formatting and safe fixes |
| `uv run poe typecheck` | mypy --strict |
| `uv run poe test` | pytest |
| `uv run poe audit` | pip-audit against the locked dependency set |
| `uv run poe security` | gitleaks, Ruff `S` rules, SOPS plaintext guard |
| `uv run poe k8s-check` | Render both overlays, then kubeconform and kube-linter |
| `uv run poe docs` | Serve these docs locally |
| `uv run poe docs-build` | Strict docs build (same as CI) |
| `uv run poe db-upgrade` | Apply migrations |
| `uv run poe migration "..."` | Autogenerate a migration |
| `uv run poe changelog` | Regenerate `CHANGELOG.md` |
| `uv run poe hooks` | Install pre-commit and commit-msg hooks |

Run `uv run poe` to list every task.
