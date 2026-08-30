"""Payment attempts - append-only.

Two rules are enforced structurally here:

*Rule 6, append-only.* A failed attempt is never mutated into a retry. Each
attempt is its own row, so the audit trail shows "tried, insufficient funds,
tried again, succeeded" rather than a single row that claims it always worked.

*Rule 9, idempotency.* ``uq_payments_provider_provider_payment_id`` means a
redelivered Razorpay webhook cannot create a second row for the same payment.
Providers retry aggressively; the database, not the handler, is the backstop.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import PaymentProvider, PaymentStatus

if TYPE_CHECKING:
    from app.models.order import Order


class Payment(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        # The idempotency backbone. Partial, because a PENDING attempt has no
        # provider_payment_id yet (only a payment-link id).
        Index(
            "uq_payments_provider_provider_payment_id",
            "provider",
            "provider_payment_id",
            unique=True,
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
        Index("ix_payments_order_id_created_at", "order_id", "created_at"),
        Index("ix_payments_business_id_status", "business_id", "status"),
        # Operational queue: "which double-charges still owe a refund?"
        Index(
            "ix_payments_needs_refund",
            "business_id",
            postgresql_where=text("needs_refund = true"),
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider: Mapped[PaymentProvider] = mapped_column(
        pg_enum(PaymentProvider, "payment_provider"),
        nullable=False,
        server_default=PaymentProvider.RAZORPAY.value,
    )

    # Razorpay ids: pay_xxx / order_xxx / plink_xxx respectively.
    provider_payment_id: Mapped[str | None] = mapped_column(String(128))
    provider_order_id: Mapped[str | None] = mapped_column(String(128))
    provider_payment_link_id: Mapped[str | None] = mapped_column(String(128))
    payment_url: Mapped[str | None] = mapped_column(Text)

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )

    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"),
        nullable=False,
        server_default=PaymentStatus.PENDING.value,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)

    # Rule 7: customer paid twice. The second SUCCESS is recorded truthfully,
    # flagged, and left for a human - it does NOT re-mark the order paid, and
    # it is never silently dropped.
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    needs_refund: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # Verbatim provider payload for the successful/failed transition. The
    # reconciliation record when a customer disputes what happened.
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    order: Mapped[Order] = relationship(back_populates="payments")

    @property
    def is_successful(self) -> bool:
        return self.status is PaymentStatus.SUCCESS

    @property
    def is_open(self) -> bool:
        """Still awaiting an outcome - a new attempt would be a duplicate."""
        return self.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING)
