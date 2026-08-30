"""Fulfillment reads."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.models.enums import FulfillmentStatus
from app.models.fulfillment import Fulfillment
from app.repositories.base import BaseRepository


class FulfillmentRepository(BaseRepository[Fulfillment]):
    model = Fulfillment

    async def get_for_order(self, order_id: uuid.UUID) -> Fulfillment | None:
        """The current fulfillment record for an order.

        Newest-first because a failed delivery followed by a retry produces two
        rows and the latest one is the live state.
        """
        stmt = (
            self._scoped()
            .where(Fulfillment.order_id == order_id)
            .order_by(Fulfillment.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_pending(self, *, limit: int = 100) -> Sequence[Fulfillment]:
        """The human work queue: paid orders awaiting delivery."""
        stmt = (
            self._scoped()
            .where(
                Fulfillment.status.in_(
                    [FulfillmentStatus.PENDING, FulfillmentStatus.READY]
                )
            )
            .order_by(Fulfillment.created_at)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
