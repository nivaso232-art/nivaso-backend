"""Authentication — JWT token issuance.

Two login flows:
  POST /auth/login              — business admin (username = business slug)
  POST /auth/super-admin/login  — Nivaso super-admin
"""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.core.errors import AuthError
from app.core.jwt import create_token
from app.repositories.business_admins import BusinessAdminRepository
from app.repositories.businesses import BusinessRepository

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class BusinessTokenOut(TokenOut):
    business_slug: str
    business_name: str
    username: str


@router.post("/login", response_model=BusinessTokenOut)
async def business_login(
    body: LoginIn,
    session: AsyncSession = Depends(get_session),
) -> BusinessTokenOut:
    """Authenticate a business admin and return a JWT."""
    repo = BusinessAdminRepository(session)
    admin = await repo.get_by_username(body.username)
    if admin is None:
        raise AuthError("Invalid username or password.")

    if not bcrypt.checkpw(body.password.encode(), admin.password_hash.encode()):
        raise AuthError("Invalid username or password.")

    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_id(admin.business_id)
    if biz is None:
        raise AuthError("Business not found.")

    token = create_token(sub=admin.username, role="admin", business_slug=biz.slug)
    return BusinessTokenOut(
        access_token=token,
        business_slug=biz.slug,
        business_name=biz.name,
        username=admin.username,
    )


@router.post("/super-admin/login", response_model=TokenOut)
async def super_admin_login(body: LoginIn) -> TokenOut:
    """Authenticate the super-admin and return a JWT."""
    import hmac as _hmac
    username_ok = _hmac.compare_digest(body.username, settings.super_admin_username)
    password_ok = _hmac.compare_digest(body.password, settings.super_admin_password)
    if not (username_ok and password_ok):
        raise AuthError("Invalid super-admin credentials.")

    token = create_token(sub=body.username, role="super_admin")
    return TokenOut(access_token=token)
