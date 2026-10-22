"""Liveness (/healthz) and readiness (/readyz) endpoints."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe. No dependency checks."""
    return {"status": "ok"}


async def _check_database(request: Request) -> None:
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _check_redis(request: Request) -> None:
    redis: Redis = request.app.state.redis
    if not await redis.ping():
        msg = "redis PING returned false"
        raise RuntimeError(msg)


@router.get("/readyz", response_model=None)
async def readyz(request: Request) -> JSONResponse:
    """Readiness probe. 200 when Postgres and Redis respond, otherwise 503."""
    checks: dict[str, str] = {}
    healthy = True
    for name, check in (("database", _check_database), ("redis", _check_redis)):
        try:
            await check(request)
        except Exception:
            logger.exception("readiness check failed", extra={"check": name})
            checks[name] = "fail"
            healthy = False
        else:
            checks[name] = "ok"
    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if healthy else "unavailable", "checks": checks},
    )
