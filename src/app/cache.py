"""Redis client factory and request-scoped dependency."""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis


def create_redis(redis_url: str) -> Redis:
    """Create the async Redis client. Call ``aclose()`` on shutdown."""
    return Redis.from_url(redis_url, decode_responses=True)


async def get_redis(request: Request) -> Redis:
    """FastAPI dependency returning the shared Redis client from ``app.state``."""
    client: Redis = request.app.state.redis
    return client


RedisDep = Annotated[Redis, Depends(get_redis)]
