"""Redis client factory and request-scoped dependency."""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.asyncio.sentinel import Sentinel

from app.config import Settings


def parse_sentinel_hosts(raw: str) -> list[tuple[str, int]]:
    """Parse a comma-separated ``host:port`` string into Sentinel address tuples.

    Raises ``ValueError`` naming the offending entry on malformed input (missing
    host, missing/invalid port, or a port outside 1..65535).
    """
    hosts: list[tuple[str, int]] = []
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            raise ValueError(f"empty sentinel host entry in {raw!r}")
        host, sep, port_str = item.rpartition(":")
        if not sep or not host:
            raise ValueError(f"sentinel host {item!r} must be 'host:port'")
        try:
            port = int(port_str)
        except ValueError:
            msg = f"sentinel host {item!r} has a non-integer port"
            raise ValueError(msg) from None
        if not 1 <= port <= 65535:
            raise ValueError(f"sentinel host {item!r} port out of range 1..65535")
        hosts.append((host, port))
    return hosts


def create_redis(settings: Settings) -> Redis:
    """Build the shared async Redis client (Sentinel master, or plain URL).

    Call ``aclose()`` on shutdown.
    """
    if settings.redis_sentinel_hosts:
        hosts = parse_sentinel_hosts(settings.redis_sentinel_hosts)
        # redis.asyncio.sentinel is unstubbed upstream, so Sentinel(...) is an
        # untyped call and master_for(...) returns Any.
        sentinel = Sentinel(  # type: ignore[no-untyped-call]  # redis has no stubs for Sentinel
            hosts,
            sentinel_kwargs={"password": settings.redis_password},
            password=settings.redis_password,
            decode_responses=True,
        )
        master: Redis = sentinel.master_for(settings.redis_master_name)
        return master
    if settings.redis_url is None:  # guaranteed by Settings validator
        raise RuntimeError("redis_url must be set when not in Sentinel mode")
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis(request: Request) -> Redis:
    """FastAPI dependency returning the shared Redis client from ``app.state``."""
    client: Redis = request.app.state.redis
    return client


RedisDep = Annotated[Redis, Depends(get_redis)]
