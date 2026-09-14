"""Coupon catalog entity.

A tenant-owned discount code redeemable against orders. Mirrors ``Product`` in
mixin composition and the per-business-unique code pattern (``sku`` there,
``code`` here). Carries its own ``custom_fields`` JSONB column validated
against admin-defined ``FieldDefinition`` rows (entity_type='coupon') via
``app/services/custom_fields.py``.

Local enums (``CouponStatus``, ``CouponDiscountType``) live in this module
rather than ``app/models/enums.py`` so this file can be developed
independently of unrelated changes to that shared module (same rationale as
``app/models/field_definition.py``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum

if TYPE_CHECKING:
    pass


class CouponStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"


class CouponDiscountType(StrEnum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class Coupon(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_coupons_business_id_code"),
        Index("ix_coupons_business_id_status", "business_id", "status"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    discount_type: Mapped[CouponDiscountType] = mapped_column(
        pg_enum(CouponDiscountType, "coupon_discount_type"), nullable=False
    )
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    max_uses: Mapped[int | None] = mapped_column(nullable=True)
    used_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    min_order_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CouponStatus] = mapped_column(
        pg_enum(CouponStatus, "coupon_status"),
        nullable=False,
        server_default=CouponStatus.ACTIVE.value,
    )
    custom_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    @property
    def is_redeemable(self) -> bool:
        return self.status is CouponStatus.ACTIVE
