"""Application configuration, loaded from environment variables.

Everything that changes between local development and the cloud lives here so
the rest of the code never has to care *where* it runs. The two values you are
most likely to override are:

* DATABASE_URL          -> sqlite locally, Postgres in the cloud
* PROCESSING_DELAY_SECONDS -> 30 as the spec requires; set it lower in tests
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Async SQLAlchemy URL. SQLite (a single file) needs zero setup locally;
    # switching to Postgres in the cloud is just a different URL, e.g.
    # postgresql+asyncpg://user:pass@host:5432/dbname
    database_url: str = "sqlite+aiosqlite:///./transactions.db"

    # The spec requires a 30-second processing delay to simulate an external
    # API call. Kept configurable so the test suite can run in milliseconds.
    processing_delay_seconds: float = 30.0

    # How often the background worker scans the DB for work that is due. A few
    # seconds is plenty and keeps idle CPU near zero.
    worker_poll_interval_seconds: float = 2.0

    # Returned by the health check so you can tell which build is live.
    service_name: str = "transaction-webhook-service"

    @field_validator("database_url")
    @classmethod
    def normalise_async_driver(cls, url: str) -> str:
        """Cloud hosts (Render, Heroku, Railway) expose Postgres URLs as
        `postgres://...` or `postgresql://...`, but async SQLAlchemy needs the
        asyncpg driver spelled out. Rewrite the scheme so operators can paste
        the provider's URL verbatim and it just works."""
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
