from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from zeno.db.models import Base

_default_engine: AsyncEngine | None = None
_default_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    if url := os.environ.get("ZENO_DATABASE_URL"):
        return url
    path = Path.cwd() / ".zeno" / "zeno.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def init_engine(
    url: str | None = None,
    *,
    echo: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """
    Create (or return cached) global async engine and session factory.
    """
    global _default_engine, _default_factory
    dsn = url or get_database_url()
    # Ensure parent directory exists for file URLs (re-read env path if any)
    if dsn.startswith("sqlite+aiosqlite:///") and "?" not in dsn.split("///", 1)[-1]:
        raw_path = dsn.removeprefix("sqlite+aiosqlite:///")
        p = Path(raw_path)
        if p.suffix:  # looks like a file path
            p.parent.mkdir(parents=True, exist_ok=True)

    _default_engine = create_async_engine(
        dsn,
        echo=echo,
    )
    _default_factory = async_sessionmaker(
        _default_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _default_engine, _default_factory


def get_engine() -> AsyncEngine:
    if _default_engine is None:
        init_engine()
    assert _default_engine is not None
    return _default_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _default_factory is None:
        init_engine()
    assert _default_factory is not None
    return _default_factory


def reset_engine() -> None:
    """Tests only — drop global engine state."""
    global _default_engine, _default_factory
    _default_engine = None
    _default_factory = None


async def dispose_db_engine() -> None:
    """Dispose the global async engine (e.g. CLI /quit)."""
    global _default_engine, _default_factory
    eng = _default_engine
    _default_engine = None
    _default_factory = None
    if eng is not None:
        await eng.dispose()


async def get_async_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def create_all_tables() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
