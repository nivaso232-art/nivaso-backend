"""Customer identity, split across two tables.

``customers`` is the *person*. ``customer_channels`` are the *handles* they
reach you through. That split is what makes edge case 21 solvable: the same
human on WhatsApp (``919876543210``) and Telegram (``78456321``) is two
``customer_channels`` rows pointing at one ``customers`` row.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import Channel

if TYPE_CHECKING:
    from app.models.business import Business
    from app.models.conversation import Conversation
    from app.models.order import Order


class Customer(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        # Partial unique: many customers may have no phone yet (a Telegram-only
        # user), but a given phone identifies one customer per business.
        Index(
            "uq_customers_business_id_phone",
            "business_id",
            "phone",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
        ),
    )

    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    business: Mapped[Business] = relationship(back_populates="customers")
    channels: Mapped[list[CustomerChannel]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", passive_deletes=True
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", passive_deletes=True
    )
    orders: Mapped[list[Order]] = relationship(back_populates="customer")

    @property
    def display_name(self) -> str:
        return self.name or self.phone or "Customer"


class CustomerChannel(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "customer_channels"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "channel",
            "external_user_id",
            name="uq_customer_channels_business_id_channel_external_user_id",
        ),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, "channel"), nullable=False)

    # The provider's own id for this user. WhatsApp: the wa_id ("919876543210").
    # Telegram: the numeric chat/user id. Stored bare - the channel column
    # already disambiguates, so no "whatsapp:" prefix is needed.
    external_user_id: Mapped[str] = mapped_column(String(128), nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    customer: Mapped[Customer] = relationship(back_populates="channels")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="customer_channel", passive_deletes=True
    )
