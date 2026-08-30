"""FastAPI shared dependencies.

``get_db`` is the session dependency for all routes. Writes that span
multiple rows must go through a :class:`~app.core.uow.UnitOfWork`; the
dependency itself does not commit.

``require_internal_key`` guards admin routes. It is a dependency, not
middleware, so it can be applied at the router level without affecting
webhook routes that use their own signature verification.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AuthError, NotFoundError
from app.core.security import verify_internal_api_key
from app.models.business import Business
from app.repositories.businesses import BusinessRepository

log = structlog.get_logger(__name__)


async def get_session(
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    return db


async def require_internal_key(
    x_internal_key: str | None = Header(default=None, alias="X-Internal-Key"),
) -> None:
    """Raise 401 if the caller did not supply the correct internal API key."""
    verify_internal_api_key(
        expected=settings.internal_api_key, provided=x_internal_key
    )


async def get_business(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> Business:
    """Resolve a business slug to an active Business, or raise 404."""
    repo = BusinessRepository(session)
    return await repo.get_active_or_raise(slug)
