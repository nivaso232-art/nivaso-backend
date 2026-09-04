"""Per-business plan assignment and feature-flag overrides.

One row per business, created automatically when a business is registered.
The ``plan`` column selects a baseline from ``PLAN_DEFAULTS``; ``overrides``
adds or restricts individual flags on top of that baseline.

Overrides are intentionally kept in JSONB rather than discrete columns so
adding a new flag requires no schema migration — only a code change to
``flags.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if __name__ == "__main__":
    pass


class BusinessEntitlement(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_entitlements"

    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Generic plan tier — free | starter | pro | enterprise.
    # Stored as plain text (not a pg enum) so adding a new tier needs
    # no ALTER TYPE migration.
    plan: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="free"
    )

    # Per-business deviations from the plan baseline.
    overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # Audit: which super-admin actor last modified this row.
    granted_by: Mapped[str | None] = mapped_column(Text)
