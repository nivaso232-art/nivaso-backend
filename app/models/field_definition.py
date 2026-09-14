"""Admin-defined custom fields.

Per-business, per-entity-type schema definitions that let each business attach
its own dynamic attributes to Product/Service/Offer/Coupon rows (e.g. a
gaming business defining "platform" as a SELECT field on products, a
services business defining "duration_minutes" as a NUMBER field). The
definitions live here; the values they validate are stored wherever the
owning entity keeps its free-form JSONB (e.g. ``Product.metadata_``).

Enums for this feature live in this module rather than ``app/models/enums.py``
so this file can be developed independently of unrelated changes to that
shared module.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum


class FieldEntityType(StrEnum):
    PRODUCT = "product"
    SERVICE = "service"
    OFFER = "offer"
    COUPON = "coupon"


class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SELECT = "select"
    MULTISELECT = "multiselect"
    DATE = "date"


class FieldDefinition(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "field_definitions"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "entity_type", "key", name="uq_field_definitions_business_entity_key"
        ),
        Index("ix_field_definitions_business_entity", "business_id", "entity_type"),
    )

    entity_type: Mapped[FieldEntityType] = mapped_column(
        pg_enum(FieldEntityType, "field_entity_type"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[FieldType] = mapped_column(
        pg_enum(FieldType, "field_type"), nullable=False
    )
    options: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    required: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(nullable=False, server_default="0")
