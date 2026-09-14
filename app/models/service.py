"""Service catalog.

Mirrors ``app/models/product.py`` closely: a tenant-owned catalog entity with
a price, a status enum, and a JSONB bag for admin-defined custom fields (see
``app/models/field_definition.py`` / ``app/services/custom_fields.py``).

Unlike ``Product.metadata_`` (mapped to a DB column literally named
``metadata``), this entity's JSONB column is named ``custom_fields`` both as
the Python attribute and the DB column - no aliasing needed.

``ServiceStatus`` lives here rather than in ``app/models/enums.py``, following
the same locally-scoped-enum convention used by ``FieldEntityType``/
``FieldType`` in ``app/models/field_definition.py``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum


class ServiceStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class Service(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "services"
    __table_args__ = (
        Index("ix_services_business_id_status", "business_id", "status"),
        CheckConstraint("price >= 0", name="price_non_negative"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )
    duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    category: Mapped[str | None] = mapped_column(String(128))

    status: Mapped[ServiceStatus] = mapped_column(
        pg_enum(ServiceStatus, "service_status"),
        nullable=False,
        server_default=ServiceStatus.ACTIVE.value,
    )

    custom_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
