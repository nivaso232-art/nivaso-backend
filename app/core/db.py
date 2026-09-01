"""Async SQLAlchemy engine + session wiring for Supabase Postgres.

Read the NullPool / statement_cache_size comment below before changing
anything here - it is the difference between a working app and an
intermittent ``DuplicatePreparedStatementError``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings


def build_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine safe to use behind Supabase's Supavisor pooler.

    Two settings are mandatory when connecting through the *transaction*
    pooler (port 6543):

    ``poolclass=NullPool``
        Supavisor already pools connections server-side. A second client-side
        pool holds sessions open that Supavisor wants to recycle, which burns
        the connection quota for no benefit.

    ``statement_cache_size=0`` / ``prepared_statement_cache_size=0``
        The transaction pooler may route consecutive statements on the same
        logical connection to *different* backend sessions. asyncpg's prepared
        statement cache assumes session affinity, so it eventually references
        a prepared statement the new backend has never seen - surfacing as
        ``DuplicatePreparedStatementError`` or ``InvalidSQLStatementNameError``
        under concurrency. Disabling the cache is the supported workaround.
    """
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            # Supabase terminates idle server-side; keep our own timeout tighter.
            "timeout": 30,
        },
    )


engine: AsyncEngine = build_engine(
    str(settings.database_url), echo=settings.db_echo
)

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session, committed on a clean response.

    Writes are still authored through :class:`app.core.uow.UnitOfWork` for the
    explicit boundary and rollback-on-error semantics. But FastAPI dependencies
    like ``get_business`` issue a read *before* the handler's UnitOfWork runs,
    which autobegins a transaction — so that UnitOfWork sees an existing
    transaction, treats itself as a nested passthrough, and never commits. The
    commit here is the backstop that makes those handler writes durable; when a
    UnitOfWork already owns and commits its transaction, this is a harmless
    no-op. (Webhook handlers use ``SessionFactory`` directly and are unaffected.)
    """
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def dispose_engine() -> None:
    """Close pooled connections. Called from the FastAPI lifespan shutdown."""
    await engine.dispose()
