"""Feature access requests raised by client-admins.

When a business wants a capability beyond their current plan they submit a
FeatureRequest. The super-admin reviews the queue and approves or denies.
Approval writes the flag directly into ``business_entitlements.overrides``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class FeatureRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "feature_requests"

    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The FeatureFlag key being requested, e.g. "channel.whatsapp".
    feature: Mapped[str] = mapped_column(String(128), nullable=False)

    # Business owner's justification for needing this capability.
    reason: Mapped[str | None] = mapped_column(Text)

    # pending | approved | denied
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending", index=True
    )

    # Super-admin who reviewed this request.
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Optional note from the super-admin (visible to the business).
    notes: Mapped[str | None] = mapped_column(Text)
