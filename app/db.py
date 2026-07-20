"""Database and Redis connectivity.

Provides the async SQLAlchemy engine/session factory and a shared Redis client.
Concrete ORM models live in ``app.models`` and register themselves on ``Base``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models (used as Alembic target metadata)."""


engine: AsyncEngine = create_async_engine(
    str(settings.database_url),
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a scoped async session."""
    async with SessionLocal() as session:
        yield session


def get_redis() -> aioredis.Redis:
    """Return a Redis client using the configured URL.

    ``redis.asyncio`` maintains an internal connection pool, so a fresh client
    object here is cheap and shares connections under the hood.
    """
    return aioredis.from_url(str(settings.redis_url), decode_responses=True)
