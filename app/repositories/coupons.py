"""Coupon reads/writes — tenant-scoped catalog access for discount codes."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.coupon import Coupon, CouponStatus
from app.repositories.base import BaseRepository


class CouponRepository(BaseRepository[Coupon]):
    model = Coupon

    async def get_by_code(self, code: str) -> Coupon | None:
        stmt = self._scoped().where(Coupon.code == code)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[Coupon]:
        stmt = (
            self._scoped()
            .where(Coupon.status == CouponStatus.ACTIVE)
            .order_by(Coupon.created_at)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()
