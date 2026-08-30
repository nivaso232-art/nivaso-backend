"""Raw inbound webhook log.

Not in the original table list, added deliberately. It does three jobs no
other table can:

1. **Idempotency** (rule 9). ``uq_webhook_events_source_external_event_id``
   turns a redelivery into a no-op INSERT conflict. Meta and Razorpay both
   retry on any non-2xx, so redelivery is the normal case, not the edge case.
2. **Fast ack.** The handler verifies the signature, writes this row, enqueues
   work, and returns 200. The LLM turn happens after the provider has already
   been acknowledged, so a slow agent never causes a duplicate delivery.
3. **Replay.** ``payload`` is the verbatim body. When something goes wrong in
   production the event can be re-run against fixed code instead of being
   reconstructed from log lines.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import WebhookSource, WebhookStatus


class WebhookEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_event_id",
            name="uq_webhook_events_source_external_event_id",
        ),
        Index("ix_webhook_events_status_created_at", "status", "created_at"),
        Index("ix_webhook_events_business_id_source", "business_id", "source"),
    )

    source: Mapped[WebhookSource] = mapped_column(
        pg_enum(WebhookSource, "webhook_source"), nullable=False
    )

    # The provider's event id. WhatsApp has no true event id, so the handler
    # synthesises one from the message id(s) in the payload - see
    # app/channels/whatsapp/parser.py.
    external_event_id: Mapped[str] = mapped_column(String(191), nullable=False)

    # NULL until the payload is resolved to a tenant. It has to be nullable:
    # the row is written *before* parsing, precisely so an unparseable payload
    # is still recorded.
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="SET NULL")
    )

    # Always true in practice - the handler rejects with 401 before writing if
    # verification fails. Stored so a future "log but don't trust" mode, and
    # any audit of it, is a column read rather than a code change.
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[WebhookStatus] = mapped_column(
        pg_enum(WebhookStatus, "webhook_status"),
        nullable=False,
        server_default=WebhookStatus.RECEIVED.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
