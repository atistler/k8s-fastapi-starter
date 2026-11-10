"""Async SQLAlchemy engine, session factory, and request-scoped session dependency."""

from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import parse_qsl, urlsplit

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# libpq-only query parameters that asyncpg does not accept as connect() kwargs
# (e.g. CNPG's generated `uri` key can carry `sslmode=`). Reject these by name
# rather than blanket-banning every `?` so non-libpq params pass through.
_LIBPQ_ONLY_QUERY_KEYS = frozenset(
    {
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "sslpassword",
        "gssencmode",
        "sslnegotiation",
    }
)


def create_engine(database_url: str) -> AsyncEngine:
    """Create the async engine. Call ``dispose()`` on shutdown.

    Normalizes a bare ``postgresql://`` scheme to ``postgresql+asyncpg://`` and
    rejects libpq-only query parameters (which asyncpg cannot accept) by name.
    """
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    query = urlsplit(database_url).query
    offending = sorted(
        {
            key
            for key, _ in parse_qsl(query, keep_blank_values=True)
            if key.lower() in _LIBPQ_ONLY_QUERY_KEYS
        }
    )
    if offending:
        raise ValueError(
            "database URL has libpq-only query parameter(s) asyncpg rejects: "
            + ", ".join(offending)
        )
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request."""
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.session_factory
    )
    async with session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
