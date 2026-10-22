"""Unhandled-exception logging and tracing-disabled app startup.

Datadog itself is driven by DD_* env vars read by ddtrace and the log formatter,
not by settings fields, so there is nothing to unit-test on the config side; see
docs/monitoring.md.
"""

import logging

import httpx
import pytest
from fastapi import FastAPI

from app.main import create_app


async def test_unhandled_exception_is_logged_and_not_swallowed(
    placeholder_env: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    # create_app() reconfigures root logging handlers; re-attach caplog's handler
    # so its capture survives that reset.
    logging.getLogger().addHandler(caplog.handler)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level("ERROR"):
            response = await client.get("/boom")

    # Error surfaces as a 500 (not swallowed) and is logged by the middleware.
    assert response.status_code == 500
    assert any(
        record.getMessage() == "unhandled exception" for record in caplog.records
    )


def test_app_builds_with_tracing_disabled(placeholder_env: None) -> None:
    # Smoke test: app constructs fine with DD_TRACE_ENABLED unset (default off).
    assert isinstance(create_app(), FastAPI)
