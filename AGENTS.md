# AGENTS.md

Canonical instructions for agents and human contributors working in this repo.
This distills the operating rules from [`SPEC.md`](SPEC.md) (§12 task runner,
§14 agent instructions). **`SPEC.md` is the source of truth** — read it first and
link to it rather than duplicating it here.

## Creating a real repository from this template (one-time)

This repo is a **starter template**. Before it becomes a real service, its
placeholders must be filled in. If you are an agent and the user asks to "set up
this repo", "initialize the template", "make this a real project", or similar, do
this:

**Placeholder format.** Every value that must be supplied is a FILL token in the
form `PLACEHOLDER_<NAME>` (always the `PLACEHOLDER_` prefix). They are valid
plain strings, so the template builds and passes CI *before* init. Find every one
with:

```
rg PLACEHOLDER_
```

The machine-readable registry is [`.template/placeholders.yml`](.template/placeholders.yml).
It defines five kinds of entry:

- **fill** — `PLACEHOLDER_*` tokens with no default (GitHub org, Backstage owner
  team and system, author name/email, AWS region/account/KMS key id, the example
  DB/Redis passwords). Replace the whole token.
- **rename** — working defaults that must become the new project's identity:
  `PROJECT_SLUG` (default `k8s-fastapi-starter`), `PROJECT_TITLE`,
  `PROJECT_DESCRIPTION`. Each lists `replace_in` and `keep_as_is`. **Do not**
  rename the slug inside `SPEC.md`, `docs/README.md`, `AGENTS.md`, or `.template/` —
  those describe the template itself.
- **keep** — intentionally generic; leave alone (the import package stays `app`).
- **runtime** — set by CD, not during init (the `0.0.0` image tag).
- **external** — set in the GitHub/cloud console, not in files (the AWS deploy
  role ARN and region, as GitHub Environment variables).

**Procedure.**

1. Read `.template/placeholders.yml`.
2. Ask the user for every `fill` value, and for the `rename` values (propose the
   defaults). `PROJECT_SLUG` must be a valid DNS-1123 label and Python
   distribution name (lowercase, digits, hyphens).
3. Apply the replacements. For `rename`, change occurrences only in `replace_in`
   and never in `keep_as_is`. Do not touch `keep`, `runtime`, or `external`.
4. Remove the `.template/` directory and the "Creating a real repository…"
   section of `docs/README.md` (the template-only parts).
5. Run `uv run poe check`, then `uv run poe k8s-check` and `uv run poe docs-build`.
6. Confirm `rg PLACEHOLDER_` returns nothing, then make the first commit
   (`chore: initialize from template`).

Do **not** fabricate real values (org names, ARNs, emails). If the user cannot
provide one yet, leave the token and tell them it is still outstanding.

## First, always

- **Read `SPEC.md` before changing anything.** Do not change a technology choice
  in §2 without asking the repo owner; record any agreed change in `docs/adr/`
  and in `SPEC.md`.
- **Do not add infrastructure that is not in `SPEC.md` §3** without first
  updating the spec.

## Use the poe tasks (local == CI)

Always drive work through the poe tasks (`SPEC.md` §12), never raw tool
invocations, so local runs match CI. Core loop before any commit:

```
uv run poe check
```

Other common targets: `uv run poe install`, `uv run poe run`, `uv run poe up` / `uv run poe down`
(local Postgres + Redis), `uv run poe audit`, `uv run poe security`, `uv run poe k8s-check`,
`uv run poe docs`, `uv run poe db-upgrade`, `uv run poe migration "..."`, `uv run poe changelog`,
`uv run poe hooks`.

## Python with uv only

- Add deps with `uv add` (or `uv add --dev`). **Never** `pip install`.
- **Never hand-edit `uv.lock`** — it is generated and committed.
- `.python-version` (3.13) is the single source of the Python version; the
  Dockerfile and CI read it.

## Typed Python

- New code must pass **`mypy --strict`** (`uv run poe typecheck`).
- No blanket `# type: ignore` — every suppression needs an error code **and** a
  reason, e.g. `# type: ignore[arg-type]  # upstream stub is wrong`.
- Follow the typing rules in `SPEC.md` §4: annotate every function, avoid `Any`
  (justify with a comment if unavoidable), use `Mapped[...]` on ORM models, and
  keep Pydantic models as the only request/response types.

## Commits — Conventional Commits

- Use Conventional Commits, e.g. `feat(items): add delete endpoint`,
  `fix(db): ...`, `docs: ...`. This feeds **git-cliff**; the changelog depends
  on it (`SPEC.md` §8). `commit-msg` enforces it via `conventional-pre-commit`.

## Migrations

- Change `src/app/models.py`, then `uv run poe migration "..."`, then **review the
  generated file** in `migrations/versions/` (autogenerate misses things).
- Keep migrations **backward-compatible — expand then contract** (old and new
  pods overlap during rollout). **Never edit a released migration**; add a new
  one. Downgrades are written but not auto-run in prod. See `SPEC.md` §7 and the
  **add-migration** skill.

## Secrets

- **Never commit plaintext secrets.** Only `deploy/**/secrets.*.yaml` may be
  committed, and only SOPS-encrypted (`SPEC.md` §9). Examples use placeholders
  only.
- If `gitleaks` or `scripts/check_sops_encrypted.py` fires, fix the cause; do
  not add an allowlist entry without asking.

## Security suppressions

- Any `# noqa: S...` or kube-linter ignore needs a written reason on the same
  line or in the ignore file, e.g. `# noqa: S608 - static SQL, no user input`.

## Tests

- **Unit tests** must run with no services.
- Tests that hit Postgres or Redis are marked `@pytest.mark.integration` and run
  in CI (and locally after `uv run poe up`). Run everything with `uv run poe test`.

## Docs

- Update `docs/` on any user-visible behavior change. `mkdocs build --strict`
  must pass (`uv run poe docs-build`).

## Monitoring

- Datadog is the monitoring stack (ddtrace). Instrument with ddtrace; do not
  introduce a different monitoring stack without a spec change.

## Releasing

- Cut releases per `SPEC.md` §8/§10 and the **release** skill: bump the
  changelog with git-cliff, commit `chore(release): vX.Y.Z`, tag and push.

## Data handling

- Do not upload repo or customer data to external AI or third-party services
  that are not on your organization's approved list (`SPEC.md` §14.13). Treat all repo
  content as potentially sensitive; use placeholders in examples.

## Project skills

Three repo skills in `.claude/skills/` encode the concrete workflows:

- **add-endpoint** — add a new FastAPI route/resource.
- **add-migration** — change the database schema.
- **release** — cut a release.
