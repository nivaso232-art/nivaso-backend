"""Super-admin API — feature request review queue.

A request's ``feature`` is normally a plain entitlement flag key (e.g.
``"module.services"``, ``"channel.whatsapp"``) and approving it writes
``{feature: True}`` into the business's overrides. As a special case, a
``feature`` value of ``"plan:<tier>"`` (e.g. ``"plan:pro"``) represents a
plan-upgrade request — approving it calls ``set_plan`` instead of touching
overrides, since plan is a distinct field, not a boolean flag.
"""

from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.entitlements.flags import VALID_PLANS, WIDGET_DEPENDENCIES
from app.entitlements.resolver import resolve
from app.models.feature_request import FeatureRequest
from app.models.module_catalog import ModuleCatalogCategory
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository
from app.repositories.feature_requests import FeatureRequestRepository
from app.repositories.module_catalog import ModuleCatalogRepository

router = APIRouter(prefix="/feature-requests", tags=["super-admin:feature-requests"])

_VALID_STATUSES = {"approved", "denied"}
_PLAN_REQUEST_PREFIX = "plan:"


async def _apply_review(
    session: AsyncSession,
    req: FeatureRequest,
    *,
    status: str,
    reviewed_by: str,
    notes: str | None,
) -> FeatureRequest:
    """Review one request and, on approval, actually grant it.

    Shared by the single-request review route and the approve-all route so
    both apply identical grant logic.
    """
    fr_repo = FeatureRequestRepository(session)
    reviewed = await fr_repo.review(req.id, status=status, reviewed_by=reviewed_by, notes=notes)
    assert reviewed is not None  # caller already confirmed it exists

    if status == "approved":
        ent_repo = EntitlementRepository(session)
        if req.feature.startswith(_PLAN_REQUEST_PREFIX):
            plan = req.feature.removeprefix(_PLAN_REQUEST_PREFIX)
            if plan not in VALID_PLANS:
                raise ValidationError(f"Unknown plan '{plan}' in request feature key.")
            await ent_repo.set_plan(req.business_id, plan, granted_by=reviewed_by)
        else:
            # Defense in depth: a widget's underlying module must still be
            # enabled at approval time too — it may have been disabled again
            # between when the request was submitted and now.
            catalog_entry = next(
                (e for e in await ModuleCatalogRepository(session).list_active() if e.key == req.feature),
                None,
            )
            if catalog_entry is not None and catalog_entry.category == ModuleCatalogCategory.WIDGET:
                dep_flag = WIDGET_DEPENDENCIES.get(req.feature)
                if dep_flag is not None:
                    ent = await ent_repo.get_or_create(req.business_id)
                    if not resolve(ent.plan, ent.overrides).get(dep_flag):
                        raise ValidationError(
                            f"Cannot approve — the module required by the '{req.feature}' "
                            f"widget is not enabled for this business.",
                            details={"widget": req.feature, "required_flag": dep_flag},
                        )

            ent = await ent_repo.get_or_create(req.business_id)
            new_overrides = {**ent.overrides, req.feature: True}
            await ent_repo.set_overrides(req.business_id, new_overrides, granted_by=reviewed_by)

    try:
        from app.repositories.audit_log import AuditLogRepository
        await AuditLogRepository(session).record(
            business_id=req.business_id,
            action=f"request_{status}",
            details={"feature": req.feature, "notes": notes},
        )
    except Exception:
        pass

    return reviewed


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

    try:
        requests = await fr_repo.list_all(status=status)
    except Exception:
        return []  # Table may not exist yet
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


def _out(req: FeatureRequest, *, business_slug: str) -> FeatureRequestOut:
    return FeatureRequestOut(
        id=str(req.id),
        business_id=str(req.business_id),
        business_slug=business_slug,
        feature=req.feature,
        reason=req.reason,
        status=req.status,
        reviewed_by=req.reviewed_by,
        notes=req.notes,
        created_at=req.created_at.isoformat(),
    )


# NOTE: /business/{slug}/approve-all must be declared BEFORE /{request_id} —
# otherwise FastAPI would try to parse "business" as a request_id UUID and
# 404 before ever reaching this route. Same precedent as /plans/defaults in
# app/api/super_admin/businesses.py.
@router.patch("/business/{slug}/approve-all", response_model=list[FeatureRequestOut])
async def approve_all_for_business(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> list[FeatureRequestOut]:
    """Approve every pending feature request for one business in one action —
    the "approve as an entire request" alternative to reviewing each
    module/integration/plan-upgrade one at a time.
    """
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    fr_repo = FeatureRequestRepository(session)
    pending = [r for r in await fr_repo.list_for_business(biz.id) if r.status == "pending"]

    out: list[FeatureRequestOut] = []
    async with UnitOfWork(session):
        for req in pending:
            reviewed = await _apply_review(
                session, req, status="approved", reviewed_by="super-admin", notes=None
            )
            out.append(_out(reviewed, business_slug=biz.slug))
    return out


@router.patch("/{request_id}", response_model=FeatureRequestOut)
async def review_feature_request(
    request_id: str,
    body: ReviewIn,
    session: AsyncSession = Depends(get_session),
) -> FeatureRequestOut:
    """Approve or deny one feature request individually.

    On approval, the flag is written directly into the business's entitlement
    overrides (or, for a "plan:<tier>" request, the plan is assigned) so the
    capability activates immediately — no manual override step.
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
        reviewed = await _apply_review(
            session, req, status=body.status, reviewed_by="super-admin", notes=body.notes
        )

    biz_repo = BusinessRepository(session)
    business = await biz_repo.get_or_raise(req.business_id)
    return _out(reviewed, business_slug=business.slug)
