"""Admin API — business management for a business's own admin.

Every route here is scoped to a single business via the {slug} path param.
Routes are protected by ``require_admin_auth`` (JWT or X-Internal-Key,
enforced at the router level in ``main.py``), which also checks that a
business-admin JWT's business_slug claim matches the {slug} in the URL.

Listing/creating businesses platform-wide lives exclusively under
``/super-admin/businesses`` — that flow also provisions the new business's
login credentials, which nothing in this module does.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import NotFoundError
from app.core.uow import UnitOfWork
from app.entitlements.flags import MIGRATION_PENDING_FLAGS, FeatureFlag
from app.entitlements.resolver import resolve
from app.models.business import Business
from app.models.enums import BusinessStatus
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository
from app.repositories.feature_requests import FeatureRequestRepository

router = APIRouter(prefix="/businesses", tags=["admin:businesses"])


# -- schemas ------------------------------------------------------------------

class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str | None
    timezone: str
    status: str
    settings: dict[str, Any]

    @classmethod
    def from_orm(cls, b: Business) -> "BusinessOut":
        return cls(
            id=str(b.id),
            slug=b.slug,
            name=b.name,
            description=b.description,
            timezone=b.timezone,
            status=b.status.value,
            settings=b.settings,
        )


class UpdateBusinessIn(BaseModel):
    name: str | None = None
    description: str | None = None
    timezone: str | None = None
    status: BusinessStatus | None = None
    settings: dict[str, Any] | None = None


# -- routes -------------------------------------------------------------------

@router.get("/{slug}", response_model=BusinessOut)
async def get_business(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> BusinessOut:
    repo = BusinessRepository(session)
    business = await repo.get_by_slug_or_raise(slug)
    return BusinessOut.from_orm(business)


@router.patch("/{slug}", response_model=BusinessOut)
async def update_business(
    slug: str,
    body: UpdateBusinessIn,
    session: AsyncSession = Depends(get_session),
) -> BusinessOut:
    repo = BusinessRepository(session)
    business = await repo.get_by_slug_or_raise(slug)

    async with UnitOfWork(session):
        if body.name is not None:
            business.name = body.name
        if body.description is not None:
            business.description = body.description
        if body.timezone is not None:
            business.timezone = body.timezone
        if body.status is not None:
            business.status = body.status
        if body.settings is not None:
            business.settings = body.settings

    return BusinessOut.from_orm(business)


# ── Entitlements (read-only for client-admin) ─────────────────────────────────

@router.get("/{slug}/entitlements")
async def get_entitlements(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the resolved entitlements for this business.

    Client-admin reads this once on load to decide which UI features to show.
    """
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)
    # Use an isolated session so the main request session is never left in
    # a broken state when the business_entitlements table doesn't exist yet.
    from app.core.db import SessionFactory
    try:
        async with SessionFactory() as iso:
            ent_repo = EntitlementRepository(iso)
            ent = await ent_repo.get_or_create(biz.id)
            # resolved() merges: code defaults → DB plan_definitions → per-business overrides
            flags = await ent_repo.resolved(biz.id)
            await iso.commit()
            return {"plan": ent.plan, "flags": flags}
    except Exception:
        # Migrations not yet applied — return enterprise-level flags so no
        # existing functionality is lost while migrations are pending.
        return {"plan": "migration_pending", "flags": MIGRATION_PENDING_FLAGS}


# ── Feature requests (raised by client-admin) ─────────────────────────────────

class FeatureRequestIn(BaseModel):
    feature: str
    reason: str | None = None


class FeatureRequestOut(BaseModel):
    id: str
    feature: str
    reason: str | None
    status: str
    notes: str | None
    created_at: str


@router.post("/{slug}/feature-requests", response_model=FeatureRequestOut, status_code=status.HTTP_201_CREATED)
async def submit_feature_request(
    slug: str,
    body: FeatureRequestIn,
    session: AsyncSession = Depends(get_session),
) -> FeatureRequestOut:
    """Submit a feature access request for super-admin review."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)
    from app.core.db import SessionFactory
    try:
        async with SessionFactory() as iso:
            fr_repo = FeatureRequestRepository(iso)
            async with UnitOfWork(iso):
                req = await fr_repo.create(
                    business_id=biz.id, feature=body.feature, reason=body.reason,
                )
            return FeatureRequestOut(
                id=str(req.id), feature=req.feature, reason=req.reason,
                status=req.status, notes=req.notes, created_at=req.created_at.isoformat(),
            )
    except Exception:
        from app.core.errors import ForbiddenError
        raise ForbiddenError(
            "Feature requests are unavailable until migrations are applied.",
            details={"hint": "Run: alembic upgrade head"},
        )


@router.get("/{slug}/feature-requests", response_model=list[FeatureRequestOut])
async def list_feature_requests(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> list[FeatureRequestOut]:
    """List this business's own feature requests and their review status."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)
    from app.core.db import SessionFactory
    try:
        async with SessionFactory() as iso:
            requests = await FeatureRequestRepository(iso).list_for_business(biz.id)
            return [
                FeatureRequestOut(
                    id=str(r.id), feature=r.feature, reason=r.reason,
                    status=r.status, notes=r.notes, created_at=r.created_at.isoformat(),
                )
                for r in requests
            ]
    except Exception:
        return []  # Table not yet created
