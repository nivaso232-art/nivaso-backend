"""Audit trail for all super-admin entitlement actions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class EntitlementAuditLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "entitlement_audit_logs"

    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # plan_changed | overrides_set | status_changed | request_approved | request_denied
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Freeform context: {"plan": "pro"}, {"overrides": {...}}, etc.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # Who performed the action — super-admin identifier or system.
    performed_by: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="super-admin"
    )
