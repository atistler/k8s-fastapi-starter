"""End-to-end items API against real Postgres and Redis.

Requires DATABASE_URL and REDIS_URL (see .env.example) and a migrated database
(`uv run poe db-upgrade`).

Integration runs against plain Redis (Docker Compose), i.e. the ``REDIS_URL``
path of ``create_redis``. The Sentinel construction path is covered by the
mocked unit test in ``tests/unit/test_cache.py``.
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http


async def test_readyz_is_ok_with_real_services(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}


async def test_item_crud_and_cache_aside(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/v1/items", json={"name": "integration-widget"})
    assert created.status_code == 201
    item_id = created.json()["id"]

    fetched = await client.get(f"/api/v1/items/{item_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "integration-widget"

    updated = await client.put(f"/api/v1/items/{item_id}", json={"name": "renamed"})
    assert updated.status_code == 200

    refetched = await client.get(f"/api/v1/items/{item_id}")
    assert refetched.json()["name"] == "renamed"

    deleted = await client.delete(f"/api/v1/items/{item_id}")
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/items/{item_id}")
    assert missing.status_code == 404


async def test_list_items_returns_list(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/items", params={"limit": 5})

    assert response.status_code == 200
    assert isinstance(response.json(), list)
