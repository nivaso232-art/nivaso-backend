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
from typing import Any

import structlog
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AuthError, NotFoundError
from app.core.security import verify_internal_api_key
from app.entitlements.resolver import resolve
from app.models.business import Business
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository

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


async def require_super_admin_key(
    x_super_admin_key: str | None = Header(default=None, alias="X-Super-Admin-Key"),
) -> None:
    """Raise 401 if the caller is not a super-admin."""
    verify_internal_api_key(
        expected=settings.super_admin_api_key, provided=x_super_admin_key
    )


async def get_business(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> Business:
    """Resolve a business slug to an active Business, or raise 404."""
    repo = BusinessRepository(session)
    return await repo.get_active_or_raise(slug)


async def get_entitlements(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the resolved entitlement dict for the current business."""
    repo = EntitlementRepository(session)
    return await repo.resolved(business.id)


def _decode_bearer(authorization: str | None) -> dict | None:
    """Extract and verify a Bearer JWT from an Authorization header.

    Returns the decoded claims dict, or raises AuthError with a specific message.
    Returns None when no Authorization header is present (lets the caller fall
    through to the API-key fallback).
    """
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        raise AuthError("Malformed Authorization header. Expected: Bearer <token>")

    token_str = authorization[7:].strip()
    if not token_str:
        raise AuthError("Bearer token is empty.")

    import jwt as _jwt
    from app.core.jwt import decode_token
    try:
        return decode_token(token_str)
    except _jwt.ExpiredSignatureError:
        raise AuthError("Token has expired. Please sign in again.")
    except _jwt.InvalidTokenError as exc:
        raise AuthError(f"Invalid token: {exc}")


async def require_admin_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_internal_key: str | None = Header(default=None, alias="X-Internal-Key"),
) -> None:
    """Accept a valid JWT (admin or super_admin role) or the X-Internal-Key header.

    For JWT business-admin tokens, also validates that the slug in the URL path
    matches the business_slug claim in the token, preventing cross-tenant access.
    """
    claims = _decode_bearer(authorization)

    if claims is not None:
        role = claims.get("role")
        if role == "super_admin":
            return  # super-admin can access any resource
        if role == "admin":
            slug_in_path = request.path_params.get("slug")
            token_slug = claims.get("business_slug")
            if slug_in_path and token_slug and slug_in_path != token_slug:
                from app.core.errors import ForbiddenError
                raise ForbiddenError("You can only access your own business.")
            return
        raise AuthError("Token does not grant admin access.")

    # Fallback: API key (backward compat for dev/CI)
    if x_internal_key:
        verify_internal_api_key(expected=settings.internal_api_key, provided=x_internal_key)
        return

    raise AuthError("Authentication required. Provide a Bearer token or X-Internal-Key.")


async def require_super_admin_auth(
    authorization: str | None = Header(default=None),
    x_super_admin_key: str | None = Header(default=None, alias="X-Super-Admin-Key"),
) -> None:
    """Accept a super_admin JWT or the X-Super-Admin-Key header."""
    claims = _decode_bearer(authorization)

    if claims is not None:
        if claims.get("role") != "super_admin":
            raise AuthError("Super-admin access required.")
        return

    if x_super_admin_key:
        verify_internal_api_key(expected=settings.super_admin_api_key, provided=x_super_admin_key)
        return

    raise AuthError("Authentication required. Provide a Bearer token or X-Super-Admin-Key.")
