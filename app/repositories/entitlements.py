"""Repository for business_entitlements."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entitlements.flags import PLAN_DEFAULTS
from app.entitlements.resolver import resolve
from app.models.business_entitlement import BusinessEntitlement
from app.repositories.plan_definitions import PlanDefinitionRepository


class EntitlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, business_id: uuid.UUID) -> BusinessEntitlement | None:
        stmt = select(BusinessEntitlement).where(
            BusinessEntitlement.business_id == business_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_create(self, business_id: uuid.UUID) -> BusinessEntitlement:
        """Return existing entitlement row, or create a free-tier one."""
        existing = await self.get(business_id)
        if existing:
            return existing
        row = BusinessEntitlement(business_id=business_id, plan="free", overrides={})
        self.session.add(row)
        await self.session.flush()
        return row

    async def resolved(self, business_id: uuid.UUID) -> dict[str, Any]:
        """Return the merged entitlements dict for a business.

        Resolution order (lowest → highest priority):
          1. Hardcoded code defaults  (PLAN_DEFAULTS in flags.py)
          2. DB plan_definitions      (super-admin editable via Plan Defaults UI)
          3. Per-business overrides   (granted by super-admin per-tenant)
        """
        row = await self.get_or_create(business_id)

        try:
            plan_def = await PlanDefinitionRepository(self.session).get(row.plan)
            if plan_def and plan_def.flags:
                base = PLAN_DEFAULTS.get(row.plan, PLAN_DEFAULTS["free"]).copy()
                base.update(plan_def.flags)   # DB plan overrides code defaults
                base.update(row.overrides)    # per-business overrides win last
                return base
        except Exception:
            pass

        # No DB plan definition — fall back to code defaults + per-business overrides.
        return resolve(row.plan, row.overrides)

    async def set_plan(
        self,
        business_id: uuid.UUID,
        plan: str,
        *,
        granted_by: str,
    ) -> BusinessEntitlement:
        row = await self.get_or_create(business_id)
        row.plan = plan
        row.granted_by = granted_by
        await self.session.flush()
        return row

    async def set_overrides(
        self,
        business_id: uuid.UUID,
        overrides: dict[str, Any],
        *,
        granted_by: str,
    ) -> BusinessEntitlement:
        row = await self.get_or_create(business_id)
        row.overrides = overrides
        row.granted_by = granted_by
        await self.session.flush()
        return row

    async def list_all(self) -> list[BusinessEntitlement]:
        stmt = select(BusinessEntitlement).order_by(BusinessEntitlement.created_at)
        return list((await self.session.execute(stmt)).scalars().all())
