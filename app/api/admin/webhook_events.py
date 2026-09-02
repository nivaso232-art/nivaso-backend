"""Admin API — webhook event log (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.models.business import Business
from app.models.enums import WebhookSource, WebhookStatus
from app.models.webhook_event import WebhookEvent

router = APIRouter(prefix="/{slug}/webhook-events", tags=["admin:webhook-events"])


class WebhookEventOut(BaseModel):
    id: str
    source: str
    external_event_id: str
    status: str
    signature_verified: bool
    error: str | None
    attempts: int
    processed_at: str | None
    created_at: str

    @classmethod
    def from_orm(cls, e: WebhookEvent) -> "WebhookEventOut":
        return cls(
            id=str(e.id),
            source=e.source.value,
            external_event_id=e.external_event_id,
            status=e.status.value,
            signature_verified=e.signature_verified,
            error=e.error,
            attempts=e.attempts,
            processed_at=e.processed_at.isoformat() if e.processed_at else None,
            created_at=e.created_at.isoformat(),
        )


@router.get("", response_model=list[WebhookEventOut])
async def list_webhook_events(
    slug: str,
    source: WebhookSource | None = None,
    status: WebhookStatus | None = None,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookEventOut]:
    stmt = (
        select(WebhookEvent)
        .where(WebhookEvent.business_id == business.id)
        .order_by(WebhookEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if source is not None:
        stmt = stmt.where(WebhookEvent.source == source)
    if status is not None:
        stmt = stmt.where(WebhookEvent.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [WebhookEventOut.from_orm(e) for e in rows]
