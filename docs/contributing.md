# Contributing

Use the same task interface locally and in CI: `uv run poe <task>`. Start with
[Getting started](getting-started.md) to configure your environment and run the API.

## Before opening a pull request

1. Install hooks once per clone with `uv run poe hooks`.
2. Make the change and update the relevant documentation.
3. Start local services with `uv run poe up`, then run `uv run poe check`.
4. Run `uv run poe check-all` for the full gate set before review. The additional
   tools it needs are listed in [Getting started](getting-started.md#prerequisites).
5. Use a Conventional Commit title, such as `docs: clarify deployment ordering`.
   See [Releasing](releasing.md#commit-messages) for the commit format.

Unit tests must work without services; the full suite includes integration tests
that need Postgres and Redis. Review the [security gates](security.md) when a check fails.

## Pre-commit hooks

Local quality gates run through [pre-commit](https://pre-commit.com/). They are the same checks CI runs, caught
before the commit is even made. Configuration is in `.pre-commit-config.yaml`; hook
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

## Working on the docs

`docs/README.md` is the single overview and the MkDocs/Backstage TechDocs homepage.
Keep detailed procedures on their topic pages and link to them from the overview.
Repository paths in code spans, such as `SPEC.md`, are relative to the repository
root; Markdown links between these pages are relative to the current document.

```bash
uv run poe docs        # preview with live reload
uv run poe docs-build  # build with strict link and configuration checks
```

Add new pages to the appropriate section in `mkdocs.yml`. Use Mermaid fences for
architecture and workflow diagrams; keep the accompanying text useful when a
reader cannot render a diagram. The [Material diagram configuration](https://squidfunk.github.io/mkdocs-material/reference/diagrams/)
enables rendering in the documentation site; GitHub also renders Mermaid fences.

The architecture decisions live in [the ADRs](adr/0001-technology-choices.md),
with `SPEC.md` as the source of truth. A technology change requires the repository
owner's agreement and an ADR.
