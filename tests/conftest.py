"""Shared pytest fixtures."""

import pytest

TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/test"
TEST_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture
def placeholder_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required settings to placeholder values for tests that build the app.

    Plain DATABASE_URL + REDIS_URL keep app-building tests in local plain mode
    (not the in-cluster CNPG PG_* / Redis Sentinel modes).
    """
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
