"""Human handoff.

Created either by the AI calling ``create_support_ticket`` when it cannot
resolve something, or automatically by ``payment_service`` when it detects a
double charge (rule 7) - the one case where a ticket is opened without anybody
asking for one.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import TicketPriority, TicketStatus

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.customer import Customer
    from app.models.order import Order


class SupportTicket(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "reference", name="uq_support_tickets_business_id_reference"
        ),
        Index(
            "ix_support_tickets_business_id_status_priority",
            "business_id",
            "status",
            "priority",
        ),
        Index("ix_support_tickets_assigned_to", "assigned_to"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL")
    )

    # Customer-facing id, e.g. TKT-2608-4M2XQ8.
    reference: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[TicketStatus] = mapped_column(
        pg_enum(TicketStatus, "ticket_status"),
        nullable=False,
        server_default=TicketStatus.OPEN.value,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        pg_enum(TicketPriority, "ticket_priority"),
        nullable=False,
        server_default=TicketPriority.MEDIUM.value,
    )

    # Free-form for now (an agent handle or email). Becomes an FK to a users
    # table once the agent console has real accounts.
    assigned_to: Mapped[str | None] = mapped_column(String(128))

    # Coarse category, e.g. GAME_ACCESS_PROBLEM, DOUBLE_PAYMENT, REFUND_REQUEST.
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    customer: Mapped[Customer] = relationship()
    conversation: Mapped[Conversation | None] = relationship()
    order: Mapped[Order | None] = relationship()

    @property
    def is_open(self) -> bool:
        return self.status not in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
