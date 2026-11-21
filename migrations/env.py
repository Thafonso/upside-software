"""Alembic environment.

Importing each module's ``repository`` registers that module's tables on the
shared metadata. Migrations sit outside ``app``, so this is the one place that
reaches into modules directly; nothing here is imported by the application.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.clients.repository  # noqa: F401  (registers "client")
import app.hunter.repository  # noqa: F401  (registers "hunter_search", "hunter_prospect")
import app.shared.jobs.models  # noqa: F401  (registers "job")
import app.shared.models  # noqa: F401  (registers "app_user")
from app.shared.config import get_settings
from app.shared.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().sync_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
