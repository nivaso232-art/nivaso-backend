"""Admin API — per-business dashboard widget customization.

The widget catalog (``DASHBOARD_WIDGET_CATALOG``) is now unified: the five
formerly-hardcoded "basic" widgets live in the same catalog as the advanced
ones, and a plan's ``ui.dashboard_widgets`` list controls all of them. There
is no longer a hardcoded always-on tier — the frontend renders only what this
endpoint returns.

Two enforcement layers run on every GET and PATCH:
  1. Plan gate — widget must be in the plan's ``ui.dashboard_widgets`` list
     (or the list must be null/unrestricted).
  2. Dependency gate — widget's entry in ``WIDGET_DEPENDENCIES`` (if any) must
     resolve to a truthy flag for this business.

Saving (PATCH) additionally requires ``ui.dashboard_customize``.

Legacy migration: saved selections written before the catalog unification lack
the basic widget keys. The GET endpoint detects this via a ``catalog_version``
marker (absent or < 2 = pre-unification) and auto-prepends the applicable
basics so existing businesses see no regression on first load.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ForbiddenError, ValidationError
from app.core.uow import UnitOfWork
from app.entitlements.flags import (
    DASHBOARD_BASIC_WIDGET_KEYS,
    DASHBOARD_WIDGET_CATALOG,
    WIDGET_DEPENDENCIES,
    FeatureFlag,
)
from app.models.business import Business
from app.repositories.entitlements import EntitlementRepository

router = APIRouter(tags=["admin:dashboard"])


class DashboardConfigOut(BaseModel):
    widgets: list[str]


class DashboardConfigIn(BaseModel):
    widgets: list[str]


async def _resolved_flags(business_id: object) -> dict[str, object]:
    from app.core.db import SessionFactory

    try:
        async with SessionFactory() as iso:
            return await EntitlementRepository(iso).resolved(business_id)  # type: ignore[arg-type]
    except Exception:
        return {}


def _dep_ok(flags: dict[str, object], key: str) -> bool:
    """Return True if the widget's feature-flag dependency is satisfied."""
    dep = WIDGET_DEPENDENCIES.get(key)
    return dep is None or bool(flags.get(dep, False))


def _build_allowed_set(flags: dict[str, object]) -> set[str]:
    """Intersect plan-allowed widgets with dependency-satisfied widgets."""
    allowed = flags.get(FeatureFlag.UI_DASHBOARD_WIDGETS)
    plan_set = set(DASHBOARD_WIDGET_CATALOG) if allowed is None else set(allowed)
    return {k for k in plan_set if _dep_ok(flags, k)}


@router.get("/{slug}/dashboard-config", response_model=DashboardConfigOut)
async def get_dashboard_config(
    business: Business = Depends(get_business),
) -> DashboardConfigOut:
    """Return this business's widget selection, enforcing plan and dependency gates.

    Defaults to every widget the plan allows (in catalog order) when the
    business has never customized. Always re-filters saved selections against
    the current plan so a downgrade can never leak widgets the business no
    longer has access to.

    Legacy migration: saved selections from before catalog unification have
    catalog_version < 2 and lack the basic widget keys. Those are detected and
    supplemented with the applicable basics transparently.
    """
    flags = await _resolved_flags(business.id)
    allowed_set = _build_allowed_set(flags)

    settings = business.settings or {}
    dashboard = settings.get("dashboard") or {}
    saved: list[str] | None = dashboard.get("widgets")
    catalog_version: int = dashboard.get("catalog_version", 1)

    if saved is None:
        # No customization yet — default to all allowed in catalog order.
        return DashboardConfigOut(
            widgets=[w for w in DASHBOARD_WIDGET_CATALOG if w in allowed_set]
        )

    if catalog_version < 2:
        # Pre-unification selection: basics were never saved, prepend them.
        basics = [
            w for w in DASHBOARD_WIDGET_CATALOG
            if w in DASHBOARD_BASIC_WIDGET_KEYS and w in allowed_set
        ]
        advanced = [w for w in saved if w in allowed_set]
        return DashboardConfigOut(widgets=basics + advanced)

    return DashboardConfigOut(widgets=[w for w in saved if w in allowed_set])


@router.patch("/{slug}/dashboard-config", response_model=DashboardConfigOut)
async def update_dashboard_config(
    body: DashboardConfigIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> DashboardConfigOut:
    """Save this business's widget selection.

    Validates every submitted key against both the plan gate and the dependency
    gate. Marks the saved data as catalog_version 2 so the legacy migration
    branch is never triggered again for this business.
    """
    flags = await _resolved_flags(business.id)

    if not flags.get(FeatureFlag.UI_DASHBOARD_CUSTOMIZE, False):
        raise ForbiddenError("Your plan does not allow dashboard customization.")

    allowed_set = _build_allowed_set(flags)
    invalid = [w for w in body.widgets if w not in allowed_set]
    if invalid:
        raise ValidationError(f"Widgets not allowed on your plan: {invalid}")

    async with UnitOfWork(session):
        current = dict(business.settings or {})
        current["dashboard"] = {"widgets": body.widgets, "catalog_version": 2}
        business.settings = current

    return DashboardConfigOut(widgets=body.widgets)
