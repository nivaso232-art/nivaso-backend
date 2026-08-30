"""Support tickets and human handoff."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.ids import generate_ticket_reference
from app.models.enums import TicketPriority, TicketStatus
from app.models.support_ticket import SupportTicket
from app.repositories.support_tickets import SupportTicketRepository

log = structlog.get_logger(__name__)

_REFERENCE_ATTEMPTS = 3

# Categories the agent may use. A closed vocabulary keeps the queue filterable -
# free-text reasons turn into 400 one-off strings within a month.
KNOWN_REASONS: frozenset[str] = frozenset(
    {
        "PRODUCT_ACCESS_PROBLEM",
        "PAYMENT_PROBLEM",
        "DOUBLE_PAYMENT",
        "PAYMENT_AMOUNT_MISMATCH",
        "REFUND_REQUEST",
        "DELIVERY_DELAY",
        "AI_COULD_NOT_RESOLVE",
        "CUSTOMER_REQUESTED_HUMAN",
        "OTHER",
    }
)


class SupportService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.tickets = SupportTicketRepository(session, business_id)

    async def create_ticket(
        self,
        *,
        customer_id: uuid.UUID,
        reason: str,
        summary: str | None = None,
        priority: TicketPriority = TicketPriority.MEDIUM,
        conversation_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        reuse_open: bool = True,
    ) -> SupportTicket:
        """Open a ticket, or return the conversation's existing open one.

        ``reuse_open`` defaults to True because a frustrated customer sending
        "still not working" four times should produce one ticket, not four. The
        agent has no way to override it - it is not a tool parameter.

        Priority is raised, never lowered, when reusing: a conversation that
        escalates from a delivery question to a double-charge should carry the
        higher priority forward.
        """
        normalized_reason = reason.strip().upper()
        if normalized_reason not in KNOWN_REASONS:
            log.warning("unknown_ticket_reason", reason=reason)
            normalized_reason = "OTHER"

        if reuse_open and conversation_id is not None:
            existing = await self.tickets.get_open_for_conversation(conversation_id)
            if existing is not None:
                if _priority_rank(priority) > _priority_rank(existing.priority):
                    existing.priority = priority
                if summary:
                    existing.metadata_ = {
                        **existing.metadata_,
                        "additional_notes": [
                            *existing.metadata_.get("additional_notes", []),
                            summary,
                        ],
                    }
                await self.session.flush()
                log.info(
                    "support_ticket_reused",
                    ticket_id=str(existing.id),
                    reference=existing.reference,
                )
                return existing

        ticket = await self._insert_with_reference(
            customer_id=customer_id,
            conversation_id=conversation_id,
            order_id=order_id,
            reason=normalized_reason,
            summary=summary,
            priority=priority,
        )

        log.info(
            "support_ticket_created",
            ticket_id=str(ticket.id),
            reference=ticket.reference,
            reason=ticket.reason,
            priority=ticket.priority.value,
        )
        return ticket

    async def _insert_with_reference(
        self,
        *,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        order_id: uuid.UUID | None,
        reason: str,
        summary: str | None,
        priority: TicketPriority,
    ) -> SupportTicket:
        last_error: IntegrityError | None = None

        for attempt in range(_REFERENCE_ATTEMPTS):
            ticket = SupportTicket(
                customer_id=customer_id,
                conversation_id=conversation_id,
                order_id=order_id,
                reference=generate_ticket_reference(),
                status=TicketStatus.OPEN,
                priority=priority,
                reason=reason,
                summary=summary,
            )
            savepoint = await self.session.begin_nested()
            try:
                await self.tickets.add(ticket)
                await savepoint.commit()
                return ticket
            except IntegrityError as exc:
                await savepoint.rollback()
                if "uq_support_tickets_business_id_reference" not in str(exc.orig):
                    raise
                last_error = exc
                log.warning("ticket_reference_collision", attempt=attempt + 1)

        raise ConflictError(
            "Could not allocate a unique ticket reference.",
            details={"attempts": _REFERENCE_ATTEMPTS},
        ) from last_error

    async def assign(self, ticket: SupportTicket, *, agent: str) -> SupportTicket:
        ticket.assigned_to = agent
        if ticket.status is TicketStatus.OPEN:
            ticket.status = TicketStatus.IN_PROGRESS
        await self.session.flush()
        return ticket

    async def resolve(
        self, ticket: SupportTicket, *, resolution: str | None = None
    ) -> SupportTicket:
        ticket.status = TicketStatus.RESOLVED
        if resolution:
            ticket.metadata_ = {**ticket.metadata_, "resolution": resolution}
        await self.session.flush()
        return ticket


def _priority_rank(priority: TicketPriority) -> int:
    return {
        TicketPriority.LOW: 0,
        TicketPriority.MEDIUM: 1,
        TicketPriority.HIGH: 2,
        TicketPriority.URGENT: 3,
    }[priority]
