# ADR 0001: Technology choices

- **Status:** Accepted
- **Scope:** The template as a whole

## Context

The template must let a new service ship to Kubernetes with no further tooling decisions. It must enforce typed Python
and security gates, and it must fit the Backstage and TechDocs setup used by the organization.

## Decision

The full decision table, with the reason for each choice and the alternatives considered, is in
`SPEC.md` (repository root), section 2. In summary:

- Python 3.13 (`.python-version`), managed with **uv**.
- **FastAPI** on **Uvicorn**, configured with **pydantic-settings**.
- **SQLAlchemy 2.x (async)** with **asyncpg**, migrated by **Alembic**. Redis through **redis-py** (`redis.asyncio`).
- **Ruff** for lint and format, **mypy --strict**, **pytest** with **httpx**.
- Security: **pip-audit** on the locked export, Ruff `S` rules, **gitleaks**, **Trivy**, **hadolint**, **kubeconform**,
  and **kube-linter**.
- **Kustomize** overlays, delivered by **Argo CD** (GitOps). Secrets are **SOPS** with AWS KMS as the git source of
  truth, synced to **AWS Secrets Manager** by a CI job and materialized into the cluster by the **External Secrets
  Operator** (SOPS → AWS Secrets Manager → ESO).
- **GitHub Actions**, **GHCR**, **Dependabot**.
- **git-cliff** and **Conventional Commits** for the changelog.
- **Backstage** `catalog-info.yml` and **MkDocs Material** for TechDocs.

## Consequences

- Teams get one set of tools across local development, CI, and release.
- Changing a choice in SPEC §2 requires updating this ADR (or adding a superseding one) and the SPEC, with the
  rationale recorded here. Do not change a choice quietly.
- Deployment is Argo CD GitOps. Secrets flow SOPS (git source of truth) → AWS Secrets Manager (synced by the
  `sync-secrets` CI job) → External Secrets Operator, which materializes the `app-secrets` Kubernetes Secret in-cluster.

**2026-10-21:** deploy model reversed from CI-apply to Argo CD GitOps; secrets runtime changed to SOPS → AWS Secrets
Manager → ESO (SOPS remains the git source of truth). This ADR was updated in place per the amended policy above rather
than superseded by a new file.
