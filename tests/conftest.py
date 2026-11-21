"""Shared test setup.

The environment is filled in *before* any ``app`` module is imported, so
``app.shared.config`` sees a valid configuration. Integration tests need a real
Postgres: the Compose one when it is reachable, otherwise testcontainers.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from cryptography.fernet import Fernet

DUMMY_DATABASE_URL = "postgresql+psycopg://upside:upside@localhost:5432/leadhunter"

ADMIN_EMAIL = "admin@upsidemkt.com.br"
ADMIN_PASSWORD = "senha-de-teste"

# Fixed on purpose: a developer's .env must not change what the suite asserts.
# Only DATABASE_URL is taken from the environment, so Compose can point at its db.
os.environ["APP_ENV"] = "test"
os.environ["SESSION_SECRET"] = "test-session-secret-value-0123456789"
os.environ["FERNET_KEY"] = Fernet.generate_key().decode("ascii")
os.environ["ADMIN_EMAIL"] = ADMIN_EMAIL
os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
os.environ["TZ"] = "America/Sao_Paulo"
os.environ.setdefault("DATABASE_URL", DUMMY_DATABASE_URL)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.shared import db as db_module  # noqa: E402
from app.shared.config import get_settings  # noqa: E402

TEST_DB_SUFFIX = "_test"


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


def _reset_caches() -> None:
    get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.get_sessionmaker.cache_clear()


def _create_test_database(base_url: str) -> str | None:
    """Recreate ``<db>_test`` on the server in ``base_url``. ``None`` if offline."""
    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError

    parts = urlsplit(base_url)
    source_name = parts.path.lstrip("/") or "postgres"
    test_name = f"{source_name}{TEST_DB_SUFFIX}"
    admin_url = _with_database(base_url, "postgres")

    try:
        engine = create_engine(
            admin_url,
            isolation_level="AUTOCOMMIT",
            connect_args={"connect_timeout": 3},
        )
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{test_name}"'))
        engine.dispose()
    except SQLAlchemyError:
        return None
    return _with_database(base_url, test_name)


def _start_testcontainer() -> tuple[str, object] | None:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        return None
    try:
        container = PostgresContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception:  # no Docker available: the caller skips
        return None
    return container.get_connection_url(), container


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A migrated, empty database. Skips the test when none can be reached."""
    from alembic import command
    from alembic.config import Config

    container: object | None = None
    url = _create_test_database(os.environ["DATABASE_URL"])
    if url is None:
        started = _start_testcontainer()
        if started is None:
            pytest.skip("nenhum Postgres disponível (Compose ou Docker)")
        url, container = started

    previous = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = url
    _reset_caches()

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    try:
        yield url
    finally:
        os.environ["DATABASE_URL"] = previous
        _reset_caches()
        if container is not None:
            container.stop()  # pyright: ignore[reportAttributeAccessIssue]


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back, so tests stay independent."""
    assert database_url
    async with db_module.get_sessionmaker()() as active:
        yield active
        await active.rollback()


@pytest.fixture
async def clean_tables(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    await session.execute(text("TRUNCATE app_user, client"))
    await session.commit()
    yield session
    await session.rollback()
