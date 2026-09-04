"""Super-admin API — business lifecycle + plan & entitlement management.

Routes:
  GET    /super-admin/businesses                  → all businesses with plans
  POST   /super-admin/businesses                  → create business + free plan
  GET    /super-admin/businesses/plans/defaults   → plan defaults grid
  GET    /super-admin/businesses/{slug}           → detail + resolved flags
  PATCH  /super-admin/businesses/{slug}/plan      → assign plan tier
  PATCH  /super-admin/businesses/{slug}/overrides → set per-flag overrides
  PATCH  /super-admin/businesses/{slug}/status    → suspend / reactivate
"""

from __future__ import annotations

import secrets
import string
from typing import Any

import bcrypt
from fastapi import APIRouter, Depends, status as http_status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.entitlements.flags import MIGRATION_PENDING_FLAGS, PLAN_DEFAULTS, VALID_PLANS
from app.entitlements.resolver import resolve
from app.models.business import Business
from app.models.enums import BusinessStatus
from app.repositories.business_admins import BusinessAdminRepository
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository
from app.repositories.plan_definitions import PlanDefinitionRepository

router = APIRouter(prefix="/businesses", tags=["super-admin:businesses"])


# ── Shared output schema ──────────────────────────────────────────────────────

class SuperAdminBusinessOut(BaseModel):
    business_id: str
    business_name: str
    business_slug: str
    business_status: str
    business_timezone: str
    plan: str
    overrides: dict[str, Any]
    resolved: dict[str, Any]
    granted_by: str | None
    created_at: str


class SuperAdminBusinessCreatedOut(SuperAdminBusinessOut):
    admin_username: str
    admin_password: str  # shown once — not stored in plaintext


def _build_out(
    biz: Business,
    plan: str,
    overrides: dict,
    granted_by: str | None,
    resolved_flags: dict | None = None,
) -> SuperAdminBusinessOut:
    if resolved_flags is not None:
        resolved = resolved_flags
    elif plan == "migration_pending":
        resolved = MIGRATION_PENDING_FLAGS
    else:
        resolved = resolve(plan, overrides)  # fallback when resolved_flags not provided
    return SuperAdminBusinessOut(
        business_id=str(biz.id),
        business_name=biz.name,
        business_slug=biz.slug,
        business_status=biz.status.value,
        business_timezone=biz.timezone,
        plan=plan,
        overrides=overrides,
        resolved=resolved,
        granted_by=granted_by,
        created_at=biz.created_at.isoformat(),
    )


async def _safe_get_ent(business_id: object) -> tuple[str, dict, str | None, dict | None]:
    """Return (plan, overrides, granted_by, resolved_flags) using an isolated session.

    resolved_flags is computed via EntitlementRepository.resolved() which respects
    DB plan_definitions on top of code defaults and per-business overrides.
    """
    from app.core.db import SessionFactory
    try:
        async with SessionFactory() as iso:
            ent_repo = EntitlementRepository(iso)
            ent = await ent_repo.get_or_create(business_id)  # type: ignore[arg-type]
            resolved_flags = await ent_repo.resolved(business_id)  # type: ignore[arg-type]
            await iso.commit()
            return ent.plan, ent.overrides, ent.granted_by, resolved_flags
    except Exception:
        return "migration_pending", {}, None, None


# ── Request schemas ───────────────────────────────────────────────────────────

class CreateBusinessIn(BaseModel):
    slug: str
    name: str
    description: str | None = None
    timezone: str = "Asia/Kolkata"
    plan: str = "free"

    @field_validator("plan")
    @classmethod
    def _valid_plan(cls, v: str) -> str:
        if v not in VALID_PLANS:
            raise ValueError(f"Unknown plan '{v}'. Valid: {sorted(VALID_PLANS)}")
        return v


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


class StatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        valid = {s.value for s in BusinessStatus}
        if v not in valid:
            raise ValueError(f"Unknown status '{v}'. Valid: {sorted(valid)}")
        return v


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SuperAdminBusinessOut])
async def list_businesses(
    session: AsyncSession = Depends(get_session),
) -> list[SuperAdminBusinessOut]:
    """All businesses with their current plan and resolved entitlements."""
    biz_repo = BusinessRepository(session)
    ent_repo = EntitlementRepository(session)

    businesses = await biz_repo.list_all()
    # Graceful: if migrations haven't run yet, show businesses with free defaults
    try:
        entitlements = {e.business_id: e for e in await ent_repo.list_all()}
    except Exception:
        entitlements = {}

    return [
        _build_out(
            biz,
            plan=entitlements[biz.id].plan if biz.id in entitlements else "migration_pending",
            overrides=entitlements[biz.id].overrides if biz.id in entitlements else {},
            granted_by=entitlements[biz.id].granted_by if biz.id in entitlements else None,
        )
        for biz in businesses
    ]


@router.post("", response_model=SuperAdminBusinessCreatedOut, status_code=http_status.HTTP_201_CREATED)
async def create_business(
    body: CreateBusinessIn,
    session: AsyncSession = Depends(get_session),
) -> SuperAdminBusinessCreatedOut:
    """Create a new client business and automatically assign its initial plan."""
    biz_repo = BusinessRepository(session)

    # Slug uniqueness check
    existing = await biz_repo.get_by_slug(body.slug)
    if existing is not None:
        raise ConflictError(f"A business with slug '{body.slug}' already exists.")

    biz = Business(
        slug=body.slug,
        name=body.name,
        description=body.description,
        timezone=body.timezone,
        status=BusinessStatus.ACTIVE,
        settings={},
    )
    ent_repo = EntitlementRepository(session)

    # Generate admin credentials (used in both try and except paths)
    alphabet = string.ascii_letters + string.digits
    plain_password = ''.join(secrets.choice(alphabet) for _ in range(12))
    password_hash = bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()

    try:
        async with UnitOfWork(session):
            await biz_repo.add(biz)
            ent = await ent_repo.set_plan(biz.id, body.plan, granted_by="super-admin")
            admin_repo = BusinessAdminRepository(session)
            await admin_repo.create(
                business_id=biz.id,
                username=body.slug,
                password_hash=password_hash,
            )
        plan, overrides, granted_by = ent.plan, ent.overrides, ent.granted_by
    except Exception:
        try:
            async with UnitOfWork(session):
                await biz_repo.add(biz)
                admin_repo = BusinessAdminRepository(session)
                await admin_repo.create(
                    business_id=biz.id,
                    username=body.slug,
                    password_hash=password_hash,
                )
        except Exception:
            plain_password = "(admin creation failed — contact support)"
        plan, overrides, granted_by = "migration_pending", {}, None

    out = _build_out(biz, plan, overrides, granted_by)
    return SuperAdminBusinessCreatedOut(
        **out.model_dump(),
        admin_username=body.slug,
        admin_password=plain_password,
    )


# NOTE: /plans/defaults must be declared BEFORE /{slug} so FastAPI matches
# the literal path first rather than treating "plans" as a slug value.
@router.get("/plans/defaults")
async def get_plan_defaults(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return plan tier defaults. DB-stored flags override hardcoded defaults flag-by-flag."""
    try:
        db_plans = await PlanDefinitionRepository(session).get_all_as_dict()
        if db_plans:
            return {
                plan: {**flags, **db_plans.get(plan, {})}
                for plan, flags in PLAN_DEFAULTS.items()
            }
    except Exception:
        pass
    return PLAN_DEFAULTS


@router.get("/{slug}", response_model=SuperAdminBusinessOut)
async def get_business(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> SuperAdminBusinessOut:
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    plan, overrides, granted_by, resolved_flags = await _safe_get_ent(biz.id)

    return _build_out(biz, plan, overrides, granted_by, resolved_flags)


@router.patch("/{slug}/plan", response_model=SuperAdminBusinessOut)
async def set_plan(
    slug: str,
    body: PlanIn,
    session: AsyncSession = Depends(get_session),
) -> SuperAdminBusinessOut:
    """Assign a plan tier to a business. Writes an audit entry."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    ent_repo = EntitlementRepository(session)
    try:
        async with UnitOfWork(session):
            ent = await ent_repo.set_plan(biz.id, body.plan, granted_by="super-admin")
            await _write_audit(session, biz.id, "plan_changed", {"plan": body.plan})
        plan, overrides, granted_by = ent.plan, ent.overrides, ent.granted_by
    except Exception:
        plan, overrides, granted_by = "migration_pending", {}, None

    return _build_out(biz, plan, overrides, granted_by)


@router.patch("/{slug}/overrides", response_model=SuperAdminBusinessOut)
async def set_overrides(
    slug: str,
    body: OverridesIn,
    session: AsyncSession = Depends(get_session),
) -> SuperAdminBusinessOut:
    """Replace the per-business override map."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    ent_repo = EntitlementRepository(session)
    try:
        async with UnitOfWork(session):
            ent = await ent_repo.set_overrides(biz.id, body.overrides, granted_by="super-admin")
            await _write_audit(session, biz.id, "overrides_set", {"overrides": body.overrides})
        plan, overrides, granted_by = ent.plan, ent.overrides, ent.granted_by
    except Exception:
        plan, overrides, granted_by = "migration_pending", {}, None

    return _build_out(biz, plan, overrides, granted_by)


@router.patch("/{slug}/status", response_model=SuperAdminBusinessOut)
async def set_status(
    slug: str,
    body: StatusIn,
    session: AsyncSession = Depends(get_session),
) -> SuperAdminBusinessOut:
    """Suspend, reactivate, or deactivate a business."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    plan, overrides, granted_by, resolved_flags = await _safe_get_ent(biz.id)

    async with UnitOfWork(session):
        biz.status = BusinessStatus(body.status)
        await _write_audit(session, biz.id, "status_changed", {"status": body.status})

    return _build_out(biz, plan, overrides, granted_by, resolved_flags)


# ── Audit helper (used by plan/override/status routes) ───────────────────────

async def _write_audit(
    session: AsyncSession,
    business_id: object,
    action: str,
    details: dict[str, Any],
) -> None:
    """Append an audit log entry if the table exists (graceful on migration lag)."""
    try:
        from app.repositories.audit_log import AuditLogRepository
        repo = AuditLogRepository(session)
        await repo.record(business_id=business_id, action=action, details=details)  # type: ignore[arg-type]
    except Exception:
        pass  # table may not exist yet during migration; don't fail the main operation
