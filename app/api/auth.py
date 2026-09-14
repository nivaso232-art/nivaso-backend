"""Authentication — JWT token issuance.

Flows:
  POST /auth/signup             — business self-signup (creates a PENDING business)
  POST /auth/login              — business admin (username = business slug)
  POST /auth/super-admin/login  — Nivaso super-admin
"""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, status as http_status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.core.errors import AuthError, ConflictError, ValidationError
from app.core.jwt import create_token
from app.core.uow import UnitOfWork
from app.models.business import Business
from app.models.enums import BusinessStatus
from app.models.feature_request import FeatureRequest
from app.repositories.business_admins import BusinessAdminRepository
from app.repositories.businesses import BusinessRepository
from app.repositories.module_catalog import ModuleCatalogRepository

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


class SignupIn(BaseModel):
    slug: str
    business_name: str
    timezone: str = "Asia/Kolkata"
    admin_username: str
    admin_password: str
    requested_modules: list[str] = []

    @field_validator("admin_password")
    @classmethod
    def _password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("admin_password must be at least 8 characters long.")
        return v


class SignupOut(BaseModel):
    business_slug: str
    business_name: str
    status: str
    requested_modules: list[str]


@router.post("/signup", response_model=SignupOut, status_code=http_status.HTTP_201_CREATED)
async def business_signup(
    body: SignupIn,
    session: AsyncSession = Depends(get_session),
) -> SignupOut:
    """Self-service business signup. Creates a PENDING business — no login until
    a super-admin approves it via PATCH /super-admin/businesses/{slug}/approve.
    No JWT is issued here.
    """
    deduped_modules = list(dict.fromkeys(body.requested_modules))

    # Widgets are deliberately excluded here — a widget's underlying module
    # must already be granted before it can be requested, which is never true
    # at signup. See ModuleCatalogRepository.get_signup_requestable_keys.
    valid_keys = await ModuleCatalogRepository(session).get_signup_requestable_keys()
    invalid = [k for k in deduped_modules if k not in valid_keys]
    if invalid:
        raise ValidationError(
            f"Unknown requestable module(s): {sorted(invalid)}.",
            details={"invalid_keys": sorted(invalid)},
        )

    biz_repo = BusinessRepository(session)

    existing = await biz_repo.get_by_slug(body.slug)
    if existing is not None:
        raise ConflictError(f"A business with slug '{body.slug}' already exists.")

    biz = Business(
        slug=body.slug,
        name=body.business_name,
        timezone=body.timezone,
        status=BusinessStatus.PENDING,
        settings={},
    )
    password_hash = bcrypt.hashpw(body.admin_password.encode(), bcrypt.gensalt()).decode()

    async with UnitOfWork(session):
        await biz_repo.add(biz)
        admin_repo = BusinessAdminRepository(session)
        await admin_repo.create(
            business_id=biz.id,
            username=body.admin_username,
            password_hash=password_hash,
        )
        for module_key in deduped_modules:
            session.add(
                FeatureRequest(
                    business_id=biz.id,
                    feature=module_key,
                    reason="Requested at signup",
                )
            )

    return SignupOut(
        business_slug=biz.slug,
        business_name=biz.name,
        status=biz.status.value,
        requested_modules=deduped_modules,
    )


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

    if biz.status == BusinessStatus.PENDING:
        raise AuthError("Your business application is still pending approval.")
    if biz.status == BusinessStatus.SUSPENDED:
        raise AuthError("Your business account has been suspended.")
    if biz.status != BusinessStatus.ACTIVE:
        raise AuthError("Your business account is not active.")

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
