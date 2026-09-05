"""Async SQLAlchemy database setup."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings


def _async_url(url: str) -> str:
    """Normalize a configured SQLAlchemy URL for the async psycopg driver."""

    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        return "sqlite+aiosqlite://" + url.removeprefix("sqlite://")
    return url


def create_engine(database_url: str | None = None, *, echo: bool | None = None) -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        _async_url(database_url or settings.database_url),
        echo=settings.sql_echo if echo is None else echo,
        pool_pre_ping=True,
    )


engine = create_engine()
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
# Descriptive aliases are convenient for worker code and dependency overrides.
async_session_factory = SessionFactory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one transaction-scoped session."""

    async with SessionFactory() as session:
        yield session


get_db = get_session


async def dispose_engine() -> None:
    await engine.dispose()
