"""Application factory. Run with: uvicorn app.main:create_app --factory

APM tracing is enabled out of process via ``ddtrace-run`` (see the Dockerfile),
gated on ``DD_TRACE_ENABLED`` which ddtrace reads itself. This keeps the app
imports clean and mypy-strict friendly, and makes tracing a no-op locally and in
tests where ``ddtrace-run`` is not used and ``DD_TRACE_ENABLED`` defaults to off.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.health import router as health_router
from app.api.items import router as items_router
from app.cache import create_redis
from app.config import Settings
from app.db import create_engine, create_session_factory
from app.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Settings are read from the environment when not given."""
    resolved = Settings() if settings is None else settings
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(resolved.database_url)
        redis = create_redis(resolved.redis_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        logger.info("starting", extra={"app_env": resolved.app_env})
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="k8s-fastapi-starter",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def log_unhandled_exceptions(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Log unhandled exceptions, then re-raise so the error is not swallowed.

        Re-raising lets Starlette return the 500 response and lets ddtrace tag
        the active span as errored (automatic APM error tracking).
        """
        try:
            return await call_next(request)
        except Exception:
            logger.exception(
                "unhandled exception",
                extra={"method": request.method, "path": request.url.path},
            )
            raise

    app.include_router(health_router)
    app.include_router(items_router)
    return app
