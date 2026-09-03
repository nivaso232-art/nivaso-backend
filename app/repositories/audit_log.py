"""Repository for entitlement_audit_logs."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entitlement_audit_log import EntitlementAuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        business_id: uuid.UUID,
        action: str,
        details: dict[str, Any],
        performed_by: str = "super-admin",
    ) -> EntitlementAuditLog:
        entry = EntitlementAuditLog(
            business_id=business_id,
            action=action,
            details=details,
            performed_by=performed_by,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_business(
        self, business_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[EntitlementAuditLog]:
        stmt = (
            select(EntitlementAuditLog)
            .where(EntitlementAuditLog.business_id == business_id)
            .order_by(EntitlementAuditLog.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_all(self, *, limit: int = 200) -> Sequence[EntitlementAuditLog]:
        stmt = (
            select(EntitlementAuditLog)
            .order_by(EntitlementAuditLog.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
