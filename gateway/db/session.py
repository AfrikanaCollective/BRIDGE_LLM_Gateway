"""Async SQLAlchemy engine/session setup.

DATABASE_URL (gateway.config.settings.database_url) defaults to a local
SQLite file for dev/tests; set it to a Postgres DSN in production (see
docker-compose.yml). Schema is created via Alembic migrations
(gateway/db/migrations/), not create_all(), outside of tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gateway.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def create_all_for_tests() -> None:
    """Create tables directly from ORM metadata. Test/dev convenience only
    — production schema changes go through Alembic (CLAUDE.md "Migrations")."""
    from gateway.db.orm import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
