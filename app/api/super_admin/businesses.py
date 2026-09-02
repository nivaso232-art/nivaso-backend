"""Super-admin API — business plan & entitlement management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.entitlements.flags import PLAN_DEFAULTS, VALID_PLANS, FeatureFlag
from app.entitlements.resolver import resolve
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository

router = APIRouter(prefix="/businesses", tags=["super-admin:businesses"])


class EntitlementOut(BaseModel):
    business_id: str
    business_name: str
    business_slug: str
    plan: str
    overrides: dict[str, Any]
    resolved: dict[str, Any]
    granted_by: str | None


class PlanIn(BaseModel):
    plan: str

    @field_validator("plan")
    @classmethod
    def _valid_plan(cls, v: str) -> str:
        if v not in VALID_PLANS:
            raise ValueError(f"Unknown plan '{v}'. Valid: {sorted(VALID_PLANS)}")
        return v


class OverridesIn(BaseModel):
    overrides: dict[str, Any]


@router.get("", response_model=list[EntitlementOut])
async def list_businesses_with_entitlements(
    session: AsyncSession = Depends(get_session),
) -> list[EntitlementOut]:
    """All businesses with their current plan and resolved entitlements."""
    biz_repo = BusinessRepository(session)
    ent_repo = EntitlementRepository(session)

    businesses = await biz_repo.list_all()
    entitlements = {e.business_id: e for e in await ent_repo.list_all()}

    result = []
    for biz in businesses:
        ent = entitlements.get(biz.id)
        plan = ent.plan if ent else "free"
        overrides = ent.overrides if ent else {}
        result.append(
            EntitlementOut(
                business_id=str(biz.id),
                business_name=biz.name,
                business_slug=biz.slug,
                plan=plan,
                overrides=overrides,
                resolved=resolve(plan, overrides),
                granted_by=ent.granted_by if ent else None,
            )
        )
    return result


@router.get("/{slug}", response_model=EntitlementOut)
async def get_business_entitlements(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> EntitlementOut:
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(biz.id)

    return EntitlementOut(
        business_id=str(biz.id),
        business_name=biz.name,
        business_slug=biz.slug,
        plan=ent.plan,
        overrides=ent.overrides,
        resolved=resolve(ent.plan, ent.overrides),
        granted_by=ent.granted_by,
    )


@router.patch("/{slug}/plan", response_model=EntitlementOut)
async def set_plan(
    slug: str,
    body: PlanIn,
    x_super_admin_key: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> EntitlementOut:
    """Assign a plan tier to a business."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    ent_repo = EntitlementRepository(session)
    async with UnitOfWork(session):
        ent = await ent_repo.set_plan(
            biz.id, body.plan, granted_by="super-admin"
        )

    return EntitlementOut(
        business_id=str(biz.id),
        business_name=biz.name,
        business_slug=biz.slug,
        plan=ent.plan,
        overrides=ent.overrides,
        resolved=resolve(ent.plan, ent.overrides),
        granted_by=ent.granted_by,
    )


@router.patch("/{slug}/overrides", response_model=EntitlementOut)
async def set_overrides(
    slug: str,
    body: OverridesIn,
    session: AsyncSession = Depends(get_session),
) -> EntitlementOut:
    """Replace the per-business override map (merges on top of plan defaults)."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    ent_repo = EntitlementRepository(session)
    async with UnitOfWork(session):
        ent = await ent_repo.set_overrides(
            biz.id, body.overrides, granted_by="super-admin"
        )

    return EntitlementOut(
        business_id=str(biz.id),
        business_name=biz.name,
        business_slug=biz.slug,
        plan=ent.plan,
        overrides=ent.overrides,
        resolved=resolve(ent.plan, ent.overrides),
        granted_by=ent.granted_by,
    )


@router.get("/plans/defaults")
async def get_plan_defaults() -> dict[str, Any]:
    """Return the default entitlements for every plan tier."""
    return PLAN_DEFAULTS
