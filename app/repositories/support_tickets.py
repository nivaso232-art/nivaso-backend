"""Support ticket reads."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.models.enums import TicketPriority, TicketStatus
from app.models.support_ticket import SupportTicket
from app.repositories.base import BaseRepository

_CLOSED = (TicketStatus.RESOLVED, TicketStatus.CLOSED)


class SupportTicketRepository(BaseRepository[SupportTicket]):
    model = SupportTicket

    async def get_by_reference(self, reference: str) -> SupportTicket | None:
        stmt = self._scoped().where(SupportTicket.reference == reference)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_open_for_conversation(
        self, conversation_id: uuid.UUID
    ) -> SupportTicket | None:
        """An already-open ticket for this conversation.

        Checked before creating a new one: a frustrated customer sending "still
        not working" five times should not open five tickets for one agent to
        triage.
        """
        stmt = (
            self._scoped()
            .where(
                SupportTicket.conversation_id == conversation_id,
                SupportTicket.status.notin_(_CLOSED),
            )
            .order_by(SupportTicket.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_open(
        self, *, priority: TicketPriority | None = None, limit: int = 100
    ) -> Sequence[SupportTicket]:
        stmt = (
            self._scoped()
            .where(SupportTicket.status.notin_(_CLOSED))
            .order_by(SupportTicket.created_at)
            .limit(limit)
        )
        if priority:
            stmt = stmt.where(SupportTicket.priority == priority)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_customer(
        self, customer_id: uuid.UUID, *, limit: int = 20
    ) -> Sequence[SupportTicket]:
        stmt = (
            self._scoped()
            .where(SupportTicket.customer_id == customer_id)
            .order_by(SupportTicket.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def reference_exists(self, reference: str) -> bool:
        stmt = select(SupportTicket.id).where(
            SupportTicket.business_id == self.business_id,
            SupportTicket.reference == reference,
        )
        return (await self.session.execute(stmt)).first() is not None
