"""Per-business messaging channel configuration.

Stores credentials for each channel a business uses (Telegram bot, WhatsApp
number). Not tenant-scoped via TenantMixin because the routing lookup
``get_by_external_id`` queries across all businesses to find which one owns
an incoming message's channel identity.
"""

from __future__ import annotations

import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.business import Business


class BusinessChannel(UUIDMixin, TimestampMixin, Base):
    """One row per (business, channel_type) pair.

    channel_type values: 'whatsapp' | 'telegram'

    credentials JSONB schema by channel:
      telegram:  {"bot_token": str, "webhook_secret": str}
      whatsapp:  {"phone_number_id": str, "access_token": str,
                  "app_secret": str, "verify_token": str}
    """

    __tablename__ = "business_channels"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "channel_type",
            name="uq_business_channels_business_id_channel_type",
        ),
        UniqueConstraint(
            "channel_type", "external_channel_id",
            name="uq_business_channels_channel_type_external_channel_id",
        ),
        Index("ix_business_channels_business_id", "business_id"),
        Index(
            "ix_business_channels_channel_type_external",
            "channel_type", "external_channel_id",
        ),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_channel_id: Mapped[str] = mapped_column(String(128), nullable=False)
    credentials: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    business: Mapped[Business] = relationship(back_populates="channels_config")
