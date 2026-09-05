"""Super-admin API — entitlement audit log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.audit_log import AuditLogRepository
from app.repositories.businesses import BusinessRepository

router = APIRouter(prefix="/audit-log", tags=["super-admin:audit"])


class AuditLogOut(BaseModel):
    id: str
    business_id: str
    business_slug: str
    action: str
    details: dict[str, Any]
    performed_by: str
    created_at: str


@router.get("", response_model=list[AuditLogOut])
async def list_audit_log(
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[AuditLogOut]:
    """Return the most recent entitlement audit entries across all businesses."""
    repo = AuditLogRepository(session)
    biz_repo = BusinessRepository(session)

    try:
        entries = await repo.list_all(limit=limit)
    except Exception:
        return []  # Table may not exist yet
    businesses = {b.id: b for b in await biz_repo.list_all()}

    return [
        AuditLogOut(
            id=str(e.id),
            business_id=str(e.business_id),
            business_slug=businesses[e.business_id].slug if e.business_id in businesses else "",
            action=e.action,
            details=e.details,
            performed_by=e.performed_by,
            created_at=e.created_at.isoformat(),
        )
        for e in entries
    ]
