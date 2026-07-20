"""Alembic environment (async).

The DB URL and target metadata are pulled from the application settings/models so
migrations always match the app configuration. Supports both offline and online
(async engine) modes.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from alembic import context
from app.config import settings

# Import models so that every table is registered on Base.metadata.
from app.models import Base  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", str(settings.database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Skip partitioned tables from autogenerate.

    ``check_result`` and its monthly partitions (``check_result_YYYY_MM``) are
    managed by a hand-written migration + a runtime partition helper, so autogenerate
    must leave them alone.
    """
    if type_ == "table":
        if name == "check_result" or name.startswith("check_result_"):
            return False
        if obj is not None and obj.info.get("skip_autogenerate"):
            return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DBAPI connection (uses a sync-style URL)."""
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
