"""Database engine and session management (async SQLAlchemy 2.0)."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")

# For SQLite we use NullPool: a fresh connection per use, closed afterwards.
# SQLite gains nothing from pooling, and NullPool sidesteps the "connection
# bound to a different event loop" problem that pooled async connections hit
# (notably across the per-test loops in the test suite).
#
# For Postgres we keep real pooling with pool_pre_ping, which discards
# connections a cloud database has silently dropped after an idle period.
#
# statement_cache_size=0 disables asyncpg's prepared-statement cache. Managed
# Postgres like Supabase sits behind a connection pooler (Supavisor/pgBouncer);
# in transaction-pooling mode cached prepared statements break with a
# DuplicatePreparedStatementError. Turning the cache off keeps us compatible
# with either Supabase pooler mode at a negligible performance cost.
if _is_sqlite:
    engine = create_async_engine(
        settings.database_url, poolclass=NullPool, echo=False
    )
else:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=False,
        connect_args={"statement_cache_size": 0},
    )

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a request-scoped session."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables if they do not exist. Fine for this assessment; a real
    service would use Alembic migrations instead."""
    # Importing here guarantees the model is registered on Base.metadata.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
