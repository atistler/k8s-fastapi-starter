"""Health endpoints that need no running services."""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.main import create_app


@pytest.fixture
async def client(placeholder_env: None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_healthz_returns_ok_without_dependencies(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_returns_503_when_dependencies_not_initialised(
    client: httpx.AsyncClient,
) -> None:
    # The lifespan is not run by ASGITransport, so engine and redis are absent.
    response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "fail", "redis": "fail"}
