"""Service reads/writes — mirrors ``app/repositories/products.py``."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.service import Service, ServiceStatus
from app.repositories.base import BaseRepository


class ServiceRepository(BaseRepository[Service]):
    model = Service

    async def list_active(
        self, *, category: str | None = None, limit: int = 50, offset: int = 0
    ) -> Sequence[Service]:
        stmt = self._scoped().where(Service.status == ServiceStatus.ACTIVE)
        if category:
            stmt = stmt.where(Service.category == category)
        stmt = stmt.order_by(Service.name).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()
