"""Database engine, session factory and the declarative base."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from sqlalchemy import DateTime, Dialect, MetaData, String, TypeDecorator, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column

from app.shared.config import get_settings

# Predictable constraint names keep Alembic autogenerate stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every module. Tables are owned by one module."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


UuidPk = Annotated[
    uuid.UUID,
    mapped_column(primary_key=True, default=uuid.uuid4),
]
CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False),
]
UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
]
Timestamptz = Annotated[datetime, mapped_column(DateTime(timezone=True))]


class EnumAsString[E: StrEnum](TypeDecorator[E]):
    """Store a ``StrEnum`` as VARCHAR and read it back as the enum.

    A plain ``String`` column would hand the application raw strings, so
    ``user.role.label`` would break right after a load.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_type: type[E], length: int = 32) -> None:
        self._enum_type = enum_type
        super().__init__(length=length)

    def process_bind_param(self, value: E | str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(self._enum_type(value).value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> E | None:
        if value is None:
            return None
        return self._enum_type(value)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)


async def session_scope() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction per request, committed on success."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close pooled connections (shutdown hook for web and worker)."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
