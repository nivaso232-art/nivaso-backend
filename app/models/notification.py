"""In-app notifications — lightweight, per-business, no delivery channel.

The first (and so far only) producer is the AI usage-limit check in
``app/services/ai_usage.py``: when a business crosses its super-admin-set
monthly spend cap, a row lands here and the admin portal surfaces it. Kept
deliberately generic (``type`` + free-text ``title``/``message``) so future
producers (e.g. a low-stock alert) don't need a new table.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Notification(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_business_id_is_read", "business_id", "is_read"),
    )

    # Stable machine-readable key, e.g. "ai_usage_limit_reached". Not an enum -
    # new notification types should never need a migration.
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[NotificationSeverity] = mapped_column(
        pg_enum(NotificationSeverity, "notification_severity"),
        nullable=False,
        server_default=NotificationSeverity.INFO.value,
    )
    is_read: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
