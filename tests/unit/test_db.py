"""Unit tests for create_engine URL handling (string-only, no DB)."""

import pytest

from app.db import create_engine


def test_scheme_is_normalized_to_asyncpg() -> None:
    engine = create_engine("postgresql://u:p@h:5432/d")
    assert engine.url.drivername == "postgresql+asyncpg"


def test_already_asyncpg_scheme_is_untouched() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@h:5432/d")
    assert engine.url.drivername == "postgresql+asyncpg"


def test_sslmode_query_param_raises_naming_it() -> None:
    with pytest.raises(ValueError, match="sslmode"):
        create_engine("postgresql+asyncpg://u:p@h:5432/d?sslmode=require")


def test_non_libpq_query_param_is_accepted() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/d?prepared_statement_cache_size=0"
    engine = create_engine(url)
    assert "prepared_statement_cache_size=0" in str(engine.url)


def test_bare_ssl_disable_query_param_is_accepted() -> None:
    url = "postgresql+asyncpg://u:p@h:5432/d?ssl=disable"
    engine = create_engine(url)
    assert "ssl=disable" in str(engine.url)
