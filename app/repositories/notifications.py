"""Repository for notifications."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    model = Notification

    async def list_for_business(
        self, *, limit: int = 50, unread_only: bool = False
    ) -> Sequence[Notification]:
        stmt = self._scoped().order_by(Notification.created_at.desc()).limit(limit)
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        return (await self.session.execute(stmt)).scalars().all()

    async def count_unread(self) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.business_id == self.business_id,
                Notification.is_read.is_(False),
            )
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def mark_read(self, notification_id: uuid.UUID) -> Notification:
        notification = await self.get_or_raise(notification_id)
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        await self.session.flush()
        return notification

    async def exists_since(self, notification_type: str, since: datetime) -> bool:
        """Whether a notification of this type has already fired since ``since``.

        Used to dedupe the AI usage-limit alert so a business over its cap
        gets one notification per month, not one per agent run.
        """
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.business_id == self.business_id,
                Notification.type == notification_type,
                Notification.created_at >= since,
            )
        )
        count = (await self.session.execute(stmt)).scalar_one()
        return count > 0
