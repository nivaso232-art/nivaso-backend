"""Admin API — in-app notifications for a business.

Currently the only producer is the AI usage-limit check
(``app/services/ai_usage.py``), but the model is generic (see
``app/models/notification.py``) so future alert types need no new endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ValidationError
from app.core.uow import UnitOfWork
from app.models.business import Business
from app.models.notification import Notification
from app.repositories.notifications import NotificationRepository

router = APIRouter(prefix="/{slug}/notifications", tags=["admin:notifications"])


class NotificationOut(BaseModel):
    id: str
    type: str
    title: str
    message: str
    severity: str
    is_read: bool
    created_at: str

    @classmethod
    def from_orm(cls, n: Notification) -> "NotificationOut":
        return cls(
            id=str(n.id),
            type=n.type,
            title=n.title,
            message=n.message,
            severity=n.severity.value,
            is_read=n.is_read,
            created_at=n.created_at.isoformat(),
        )


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    slug: str,
    unread_only: bool = False,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[NotificationOut]:
    repo = NotificationRepository(session, business.id)
    notifications = await repo.list_for_business(unread_only=unread_only)
    return [NotificationOut.from_orm(n) for n in notifications]


# NOTE: /unread-count must be declared BEFORE /{notification_id} routes so
# FastAPI matches the literal path first rather than treating "unread-count"
# as a notification_id value (same precedent as /plans/defaults in
# app/api/super_admin/businesses.py).
@router.get("/unread-count")
async def get_unread_count(
    slug: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    repo = NotificationRepository(session, business.id)
    return {"count": await repo.count_unread()}


@router.patch("/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    slug: str,
    notification_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> NotificationOut:
    try:
        nid = uuid.UUID(notification_id)
    except ValueError:
        raise ValidationError(
            "notification_id must be a valid UUID.", details={"notification_id": notification_id}
        )
    repo = NotificationRepository(session, business.id)
    async with UnitOfWork(session):
        notification = await repo.mark_read(nid)
    return NotificationOut.from_orm(notification)
