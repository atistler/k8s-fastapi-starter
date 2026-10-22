# syntax=docker/dockerfile:1
# Python version is read from .python-version (single source of truth). Keep
# PYTHON_VERSION in step with that file.
ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.12.10

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---- builder: resolve and install locked dependencies into /app/.venv ----
FROM python:${PYTHON_VERSION}-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# Dependency layer first so source edits do not invalidate the install.
COPY .python-version pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime: no build tools, non-root user ----
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# UID/GID 10001 matches the Deployment's securityContext.runAsUser (SPEC section 4).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
# Migrations and alembic.ini ship in the image so the migration Job can run
# `alembic upgrade head` from the same image (SPEC section 7).
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

# ddtrace-run enables Datadog APM auto-instrumentation out of process. It is a
# no-op when DD_TRACE_ENABLED is false (the default), so the image runs the same
# with or without a Datadog agent. ddtrace-run is installed in the venv on PATH.
CMD ["ddtrace-run", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
