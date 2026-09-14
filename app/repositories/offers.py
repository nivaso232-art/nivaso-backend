"""Offer reads/writes."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.offer import Offer, OfferStatus
from app.repositories.base import BaseRepository


class OfferRepository(BaseRepository[Offer]):
    model = Offer

    async def list_active(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[Offer]:
        stmt = (
            self._scoped()
            .where(Offer.status == OfferStatus.ACTIVE)
            .order_by(Offer.name)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()
