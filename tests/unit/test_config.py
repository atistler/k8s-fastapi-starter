"""Settings parsing from environment variables."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _clear_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every DB/Redis mode var so each test sets exactly what it needs."""
    for name in (
        "DATABASE_URL",
        "PG_HOST",
        "PG_PORT",
        "PG_USER",
        "PG_PASSWORD",
        "PG_DBNAME",
        "REDIS_URL",
        "REDIS_SENTINEL_HOSTS",
        "REDIS_MASTER_NAME",
        "REDIS_PASSWORD",
        "APP_ENV",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_required_fields_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://:p@h:6379/0")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+asyncpg://u:p@h:5432/d"
    assert settings.redis_url == "redis://:p@h:6379/0"


def test_defaults_apply_when_optional_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")

    settings = Settings(_env_file=None)

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.redis_master_name == "mymaster"


def test_optional_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings(_env_file=None)

    assert settings.app_env == "dev"
    assert settings.log_level == "debug"


# --- DB mode matrix ---------------------------------------------------------


def test_database_url_alone_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")

    settings = Settings(_env_file=None)

    assert settings.database_dsn == "postgresql+asyncpg://u:p@h:5432/d"


def test_full_pg_discrete_set_alone_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("PG_HOST", "pg-rw")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "app")
    monkeypatch.setenv("PG_PASSWORD", "secret")
    monkeypatch.setenv("PG_DBNAME", "app")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")

    settings = Settings(_env_file=None)

    assert settings.database_dsn == "postgresql+asyncpg://app:secret@pg-rw:5432/app"


def test_neither_db_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_partial_pg_discrete_set_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("PG_HOST", "pg-rw")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_dsn_percent_quotes_user_and_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("PG_HOST", "pg-rw")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "user@role")
    monkeypatch.setenv("PG_PASSWORD", "p@ss:word")
    monkeypatch.setenv("PG_DBNAME", "app")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")

    settings = Settings(_env_file=None)

    dsn = settings.database_dsn
    assert dsn == "postgresql+asyncpg://user%40role:p%40ss%3Aword@pg-rw:5432/app"
    # Composed DSN carries no query string.
    assert "?" not in dsn


# --- Redis mode matrix ------------------------------------------------------


def test_redis_sentinel_hosts_alone_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "rfs-redis:26379")

    settings = Settings(_env_file=None)

    assert settings.redis_is_sentinel is True


def test_redis_url_alone_is_not_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")

    settings = Settings(_env_file=None)

    assert settings.redis_is_sentinel is False


def test_neither_redis_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_empty_redis_master_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modes(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "rfs-redis:26379")
    monkeypatch.setenv("REDIS_MASTER_NAME", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
