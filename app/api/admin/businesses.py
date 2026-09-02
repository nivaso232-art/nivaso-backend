"""Admin API — business management.

All routes require the X-Internal-Key header (enforced at the router level
in ``main.py``). No customer-facing auth is used here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import NotFoundError
from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
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


class CreateBusinessIn(BaseModel):
    slug: str
    name: str
    description: str | None = None
    timezone: str = "Asia/Kolkata"
    settings: dict[str, Any] = {}


class UpdateBusinessIn(BaseModel):
    name: str | None = None
    description: str | None = None
    timezone: str | None = None
    status: BusinessStatus | None = None
    settings: dict[str, Any] | None = None


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[BusinessOut])
async def list_businesses(
    session: AsyncSession = Depends(get_session),
) -> list[BusinessOut]:
    repo = BusinessRepository(session)
    businesses = await repo.list_all()
    return [BusinessOut.from_orm(b) for b in businesses]


@router.post("", response_model=BusinessOut, status_code=status.HTTP_201_CREATED)
async def create_business(
    body: CreateBusinessIn,
    session: AsyncSession = Depends(get_session),
) -> BusinessOut:
    business = Business(
        slug=body.slug,
        name=body.name,
        description=body.description,
        timezone=body.timezone,
        status=BusinessStatus.ACTIVE,
        settings=body.settings,
    )
    async with UnitOfWork(session):
        repo = BusinessRepository(session)
        await repo.add(business)
    return BusinessOut.from_orm(business)


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
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(biz.id)
    return {"plan": ent.plan, "flags": resolve(ent.plan, ent.overrides)}


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
    fr_repo = FeatureRequestRepository(session)
    async with UnitOfWork(session):
        req = await fr_repo.create(
            business_id=biz.id, feature=body.feature, reason=body.reason,
        )
    return FeatureRequestOut(
        id=str(req.id), feature=req.feature, reason=req.reason,
        status=req.status, notes=req.notes, created_at=req.created_at.isoformat(),
    )


@router.get("/{slug}/feature-requests", response_model=list[FeatureRequestOut])
async def list_feature_requests(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> list[FeatureRequestOut]:
    """List this business's own feature requests and their review status."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)
    fr_repo = FeatureRequestRepository(session)
    requests = await fr_repo.list_for_business(biz.id)
    return [
        FeatureRequestOut(
            id=str(r.id), feature=r.feature, reason=r.reason,
            status=r.status, notes=r.notes, created_at=r.created_at.isoformat(),
        )
        for r in requests
    ]
