"""Runtime configuration loaded from environment variables (and a local .env file)."""

from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Two mutually exclusive modes each for Postgres and Redis:

    - **Postgres** — local dev / plain ``DATABASE_URL``
      (``postgresql+asyncpg://…``) OR the discrete ``PG_*`` set projected
      in-cluster from CloudNativePG's generated ``pg-app`` Secret, composed into
      :attr:`database_dsn`.
    - **Redis** — local dev / plain ``REDIS_URL`` OR Sentinel-mode HA
      (``REDIS_SENTINEL_HOSTS`` + ``REDIS_MASTER_NAME`` + optional
      ``REDIS_PASSWORD``).

    Field names map to environment variables case-insensitively
    (``database_url`` <- ``DATABASE_URL``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Postgres — two mutually exclusive modes:
    # (1) local dev / plain: database_url (postgresql+asyncpg://…)
    # (2) cluster: discrete fields from CNPG's generated pg-app Secret
    database_url: str | None = None
    pg_host: str | None = None
    pg_port: int | None = None
    pg_user: str | None = None
    pg_password: str | None = None
    pg_dbname: str | None = None

    # Redis — two mutually exclusive modes:
    # (1) local dev / plain: redis_url (redis://[:pw]@host:port/db)
    # (2) cluster HA: Sentinel — sentinel_hosts + master_name (+ optional password)
    redis_url: str | None = None
    redis_sentinel_hosts: str | None = None  # "h1:26379,h2:26379,h3:26379"
    redis_master_name: str = Field(default="mymaster", min_length=1)
    redis_password: str | None = None  # Sentinel + master auth

    app_env: str = "local"
    log_level: str = "INFO"

    # Datadog is configured entirely through DD_* environment variables, which
    # ddtrace (via ddtrace-run) and the log formatter read directly. They are
    # deliberately NOT mirrored as settings fields here, so there is no second,
    # drifting source of truth. See docs/monitoring.md and SPEC §5.1.

    @model_validator(mode="after")
    def _require_one_mode_each(self) -> "Settings":
        if not self.redis_sentinel_hosts and not self.redis_url:
            raise ValueError("set REDIS_SENTINEL_HOSTS or REDIS_URL")
        discrete = (
            self.pg_host,
            self.pg_port,
            self.pg_user,
            self.pg_password,
            self.pg_dbname,
        )
        if not self.database_url and not all(discrete):
            raise ValueError("set DATABASE_URL or the full PG_* discrete set")
        return self

    @property
    def redis_is_sentinel(self) -> bool:
        return bool(self.redis_sentinel_hosts)

    @property
    def database_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        # all PG_* guaranteed set by the validator when database_url is None.
        # Percent-quote BOTH user and password: CNPG's generated app-role name
        # is the DNS-label-safe `app`, so quoting the user is a no-op today — but
        # database_dsn presents itself as a general DSN composer, so a role name
        # with a URL-reserved char (`@`, `:`) must not produce a malformed DSN.
        user = quote(self.pg_user or "", safe="")
        pw = quote(self.pg_password or "", safe="")
        return (
            f"postgresql+asyncpg://{user}:{pw}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_dbname}"
        )
