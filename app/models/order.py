"""Orders and order items.

``order_items`` stores **snapshots** of product name / sku / unit price. When
GTA 5 goes from ₹229 to ₹299 tomorrow, an order placed today must still read
₹229 - history is not rewritten by a price change.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import TERMINAL_ORDER_STATUSES, OrderStatus

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.fulfillment import Fulfillment
    from app.models.payment import Payment
    from app.models.product import Product


class Order(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "reference", name="uq_orders_business_id_reference"
        ),
        CheckConstraint("subtotal >= 0", name="subtotal_non_negative"),
        CheckConstraint("discount >= 0", name="discount_non_negative"),
        CheckConstraint("total >= 0", name="total_non_negative"),
        CheckConstraint("total = subtotal - discount", name="total_matches_components"),
        Index("ix_orders_business_id_status", "business_id", "status"),
        Index("ix_orders_customer_id_created_at", "customer_id", "created_at"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Nullable: an order may be created by a human agent or an admin API call
    # with no conversation behind it.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        index=True,
    )

    # Customer-facing id, e.g. ORD-2608-7F3K9Q. See app/core/ids.py.
    reference: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus, "order_status"),
        nullable=False,
        server_default=OrderStatus.DRAFT.value,
    )

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", passive_deletes=True
    )
    payments: Mapped[list[Payment]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Payment.created_at",
    )
    fulfillments: Mapped[list[Fulfillment]] = relationship(
        back_populates="order", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_ORDER_STATUSES

    @property
    def is_paid(self) -> bool:
        return self.status in (
            OrderStatus.PAID,
            OrderStatus.FULFILLED,
            OrderStatus.REFUNDED,
        )

    def total_in_minor_units(self) -> int:
        """Paise, for Razorpay."""
        return int((self.total * 100).to_integral_value())


class OrderItem(UUIDMixin, TimestampMixin, Base):
    """No ``business_id``: an order item is meaningless outside its order, and
    the tenant is already established one hop up."""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("total = unit_price * quantity", name="total_matches_line"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL, not CASCADE: deleting a product must not delete the order line
    # that records it was once sold. The snapshot columns below keep the line
    # readable after the product is gone.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL")
    )

    # --- snapshots, frozen at purchase time -----------------------------
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_sku: Mapped[str | None] = mapped_column(String(64))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # --------------------------------------------------------------------

    quantity: Mapped[int] = mapped_column(nullable=False, server_default="1")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()
