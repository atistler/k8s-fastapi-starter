"""Runtime configuration loaded from environment variables (and a local .env file)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    ``DATABASE_URL`` and ``REDIS_URL`` are required. Field names map to
    environment variables case-insensitively (``database_url`` <- ``DATABASE_URL``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    redis_url: str
    app_env: str = "local"
    log_level: str = "INFO"

    # Datadog is configured entirely through DD_* environment variables, which
    # ddtrace (via ddtrace-run) and the log formatter read directly. They are
    # deliberately NOT mirrored as settings fields here, so there is no second,
    # drifting source of truth. See docs/monitoring.md and SPEC §5.1.
