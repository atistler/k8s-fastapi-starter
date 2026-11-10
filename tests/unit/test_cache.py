"""Unit tests for Redis client construction (no network)."""

from unittest.mock import MagicMock

import pytest

from app import cache
from app.cache import create_redis, parse_sentinel_hosts
from app.config import Settings


def test_parse_sentinel_hosts_valid() -> None:
    assert parse_sentinel_hosts("h1:26379,h2:26380,h3:26381") == [
        ("h1", 26379),
        ("h2", 26380),
        ("h3", 26381),
    ]


def test_parse_sentinel_hosts_single() -> None:
    assert parse_sentinel_hosts("rfs-redis:26379") == [("rfs-redis", 26379)]


def test_parse_sentinel_hosts_missing_port_raises() -> None:
    with pytest.raises(ValueError, match="host:port"):
        parse_sentinel_hosts("rfs-redis")


def test_parse_sentinel_hosts_non_integer_port_raises() -> None:
    with pytest.raises(ValueError, match="non-integer port"):
        parse_sentinel_hosts("rfs-redis:abc")


def test_parse_sentinel_hosts_port_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="out of range"):
        parse_sentinel_hosts("rfs-redis:70000")


def test_create_redis_sentinel_path(monkeypatch: pytest.MonkeyPatch) -> None:
    master = MagicMock(name="master")
    sentinel_instance = MagicMock(name="sentinel")
    sentinel_instance.master_for.return_value = master
    sentinel_cls = MagicMock(name="Sentinel", return_value=sentinel_instance)
    monkeypatch.setattr(cache, "Sentinel", sentinel_cls)

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h:5432/d",
        redis_sentinel_hosts="h1:26379,h2:26379",
        redis_master_name="mymaster",
        redis_password="pw",  # noqa: S106 - test fixture value, not a real secret
    )

    result = create_redis(settings)

    assert result is master
    sentinel_cls.assert_called_once()
    args, kwargs = sentinel_cls.call_args
    assert args[0] == [("h1", 26379), ("h2", 26379)]
    assert kwargs["decode_responses"] is True
    assert kwargs["password"] == "pw"  # noqa: S105 - test fixture value, not a real secret
    assert kwargs["sentinel_kwargs"] == {"password": "pw"}
    sentinel_instance.master_for.assert_called_once_with("mymaster")


def test_create_redis_plain_url_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock(name="redis-client")
    from_url = MagicMock(name="from_url", return_value=client)
    monkeypatch.setattr("redis.asyncio.Redis.from_url", from_url)

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h:5432/d",
        redis_url="redis://:p@localhost:6379/0",
    )

    result = create_redis(settings)

    assert result is client
    from_url.assert_called_once_with(
        "redis://:p@localhost:6379/0", decode_responses=True
    )
