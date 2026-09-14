"""Offers.

Discount campaigns a business can run against its catalog: percentage or
fixed-amount discounts, optionally scoped to specific products/services via
``applies_to`` (e.g. ``{"scope": "all"}`` or
``{"scope": "products", "ids": [...]}``), with an optional active window
(``starts_at``/``ends_at``).

Local ``OfferStatus``/``OfferDiscountType`` enums live in this module rather
than ``app/models/enums.py`` so this file can be developed independently of
unrelated changes to that shared module (mirrors the convention already used
by ``app/models/field_definition.py``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum


class OfferStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class OfferDiscountType(StrEnum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class Offer(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "offers"
    __table_args__ = (Index("ix_offers_business_id_status", "business_id", "status"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    discount_type: Mapped[OfferDiscountType] = mapped_column(
        pg_enum(OfferDiscountType, "offer_discount_type"), nullable=False
    )
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    applies_to: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[OfferStatus] = mapped_column(
        pg_enum(OfferStatus, "offer_status"),
        nullable=False,
        server_default=OfferStatus.DRAFT.value,
    )

    custom_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
