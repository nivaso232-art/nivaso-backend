"""Appointment scheduling.

Unlike ``Service``/``Offer``/``Coupon``, ``Appointment`` is a deliberately
static, fixed-schema entity — like ``Order``, ``Customer``, and
``SupportTicket`` — and does NOT participate in the admin-defined
custom-fields system (see ``app/models/field_definition.py`` /
``app/services/custom_fields.py``). ``AppointmentStatus`` therefore lives
here, locally, rather than in ``app/models/enums.py``, and miscellaneous
non-schema data goes in ``metadata_`` (mapped to the DB column literally
named ``metadata``) rather than a ``custom_fields`` column.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum


class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class Appointment(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_business_id_scheduled_at", "business_id", "scheduled_at"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL, not CASCADE: deleting a service must not delete the appointment
    # history that booked it.
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(nullable=False, server_default="30")

    status: Mapped[AppointmentStatus] = mapped_column(
        pg_enum(AppointmentStatus, "appointment_status"),
        nullable=False,
        server_default=AppointmentStatus.SCHEDULED.value,
    )

    notes: Mapped[str | None] = mapped_column(Text)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
