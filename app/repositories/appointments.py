"""Appointment reads and writes."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func

from app.models.appointment import Appointment
from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository[Appointment]):
    model = Appointment

    async def list_upcoming(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[Appointment]:
        stmt = (
            self._scoped()
            .where(Appointment.scheduled_at >= func.now())
            .order_by(Appointment.scheduled_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_by_customer(self, customer_id: uuid.UUID) -> Sequence[Appointment]:
        stmt = (
            self._scoped()
            .where(Appointment.customer_id == customer_id)
            .order_by(Appointment.scheduled_at.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()
