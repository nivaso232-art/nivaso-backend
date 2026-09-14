"""Add coupons table for the Coupon catalog entity.

Introduces the ``coupon_status`` and ``coupon_discount_type`` native Postgres
enum types and the ``coupons`` table: a tenant-owned discount-code entity
(code/discount_type/discount_value/max_uses/used_count/min_order_amount/
starts_at/ends_at/status), plus a ``custom_fields`` JSONB column validated
against ``field_definitions`` rows (entity_type='coupon') via
``app/services/custom_fields.py``.

This is a brand-new table with no existing data, so the downgrade is a real,
safe drop.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


COUPON_STATUS_VALUES = ("active", "expired", "disabled")
COUPON_DISCOUNT_TYPE_VALUES = ("percentage", "fixed_amount")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*COUPON_STATUS_VALUES, name="coupon_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*COUPON_DISCOUNT_TYPE_VALUES, name="coupon_discount_type").create(
        bind, checkfirst=True
    )

    op.create_table(
        "coupons",
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column(
            "discount_type",
            postgresql.ENUM(
                *COUPON_DISCOUNT_TYPE_VALUES, name="coupon_discount_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("discount_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("min_order_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*COUPON_STATUS_VALUES, name="coupon_status", create_type=False),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "custom_fields", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_coupons"),
        sa.UniqueConstraint("business_id", "code", name="uq_coupons_business_id_code"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_coupons_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_coupons_business_id", "coupons", ["business_id"])
    op.create_index(
        "ix_coupons_business_id_status", "coupons", ["business_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("coupons")

    bind = op.get_bind()
    postgresql.ENUM(name="coupon_discount_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="coupon_status").drop(bind, checkfirst=True)
