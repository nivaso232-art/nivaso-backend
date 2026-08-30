"""Alembic environment.

Two things worth knowing before editing:

1. **The URL comes from ``DATABASE_DIRECT_URL``, not ``DATABASE_URL``.**
   Migrations must not run through Supabase's transaction pooler (port 6543).
   DDL there is unreliable - the pooler can route statements in one migration
   to different backend sessions, so an ``ALTER TYPE`` and the ``ALTER TABLE``
   that depends on it may not see each other. Port 5432 is a real session.

2. ``target_metadata`` comes from ``app.models``, whose ``__init__`` imports
   every model. A model not reachable from there is invisible to autogenerate
   and gets silently omitted from migrations.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", str(settings.database_direct_url))

target_metadata = Base.metadata


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Keep autogenerate focused on our own schema.

    Supabase ships extensions that create their own tables (``pgsodium``,
    ``supabase_migrations``, and so on). Without this filter, autogenerate
    proposes dropping them.
    """
    if type_ == "table" and getattr(obj, "schema", None) not in (None, "public"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Even on the direct connection, asyncpg's statement cache is pointless
        # for a one-shot migration run and only adds a failure mode.
        connect_args={"statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
