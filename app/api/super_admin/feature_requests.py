"""Super-admin API — feature request review queue."""

from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository
from app.repositories.feature_requests import FeatureRequestRepository

router = APIRouter(prefix="/feature-requests", tags=["super-admin:feature-requests"])

_VALID_STATUSES = {"approved", "denied"}


class FeatureRequestOut(BaseModel):
    id: str
    business_id: str
    business_slug: str
    feature: str
    reason: str | None
    status: str
    reviewed_by: str | None
    notes: str | None
    created_at: str


class ReviewIn(BaseModel):
    status: str
    notes: str | None = None


@router.get("", response_model=list[FeatureRequestOut])
async def list_feature_requests(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[FeatureRequestOut]:
    """List all feature requests, optionally filtered by status."""
    fr_repo = FeatureRequestRepository(session)
    biz_repo = BusinessRepository(session)

    requests = await fr_repo.list_all(status=status)
    businesses = {b.id: b for b in await biz_repo.list_all()}

    return [
        FeatureRequestOut(
            id=str(r.id),
            business_id=str(r.business_id),
            business_slug=businesses[r.business_id].slug if r.business_id in businesses else "",
            feature=r.feature,
            reason=r.reason,
            status=r.status,
            reviewed_by=r.reviewed_by,
            notes=r.notes,
            created_at=r.created_at.isoformat(),
        )
        for r in requests
    ]


@router.patch("/{request_id}", response_model=FeatureRequestOut)
async def review_feature_request(
    request_id: str,
    body: ReviewIn,
    session: AsyncSession = Depends(get_session),
) -> FeatureRequestOut:
    """Approve or deny a feature request.

    On approval, the flag is written directly into the business's entitlement
    overrides so the capability activates immediately — no manual override step.
    """
    if body.status not in _VALID_STATUSES:
        raise ValidationError(
            f"Invalid status '{body.status}'. Use: {sorted(_VALID_STATUSES)}"
        )

    try:
        rid = _uuid.UUID(request_id)
    except ValueError:
        raise NotFoundError("Feature request not found.")

    fr_repo = FeatureRequestRepository(session)
    req = await fr_repo.get(rid)
    if req is None:
        raise NotFoundError("Feature request not found.")
    if req.status != "pending":
        raise ValidationError("Only pending requests can be reviewed.")

    async with UnitOfWork(session):
        reviewed = await fr_repo.review(
            rid,
            status=body.status,
            reviewed_by="super-admin",
            notes=body.notes,
        )

        if body.status == "approved":
            ent_repo = EntitlementRepository(session)
            ent = await ent_repo.get_or_create(req.business_id)
            new_overrides = {**ent.overrides, req.feature: True}
            await ent_repo.set_overrides(
                req.business_id, new_overrides, granted_by="super-admin"
            )

    biz_repo = BusinessRepository(session)
    business = await biz_repo.get_or_raise(req.business_id)

    return FeatureRequestOut(
        id=str(reviewed.id),
        business_id=str(reviewed.business_id),
        business_slug=business.slug,
        feature=reviewed.feature,
        reason=reviewed.reason,
        status=reviewed.status,
        reviewed_by=reviewed.reviewed_by,
        notes=reviewed.notes,
        created_at=reviewed.created_at.isoformat(),
    )
