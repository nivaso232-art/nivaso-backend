"""Delivery record.

Deliberately credential-free (rule 10). Whatever the business actually hands
over - a game account, a booking slot, a download link - is referenced by
handle in ``metadata["credential_ref"]`` and stored in a real secrets manager
with encryption at rest, access control, short retention, and audit logging.

Do not add ``username`` / ``password`` / ``otp`` columns here. A plaintext
credential column in a table this widely joined is a breach waiting for a
`SELECT *`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import FulfillmentStatus

if TYPE_CHECKING:
    from app.models.order import Order


class Fulfillment(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "fulfillments"
    __table_args__ = (
        Index("ix_fulfillments_order_id", "order_id"),
        Index("ix_fulfillments_business_id_status", "business_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[FulfillmentStatus] = mapped_column(
        pg_enum(FulfillmentStatus, "fulfillment_status"),
        nullable=False,
        server_default=FulfillmentStatus.PENDING.value,
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    # Non-sensitive delivery detail only, e.g.
    #   {"credential_ref": "vault://naveen-games/accounts/8f3e",
    #    "delivery_method": "steam_family_share",
    #    "delivered_by": "agent_12"}
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    order: Mapped[Order] = relationship(back_populates="fulfillments")

    @property
    def is_delivered(self) -> bool:
        return self.status is FulfillmentStatus.DELIVERED
