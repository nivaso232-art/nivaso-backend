"""Add offers table for discount/offer campaigns.

Introduces two new native Postgres enum types (``offer_status``,
``offer_discount_type``) and the ``offers`` table: per-business discount
campaigns (percentage or fixed-amount) optionally scoped to specific catalog
items via ``applies_to`` JSONB, with an optional active window
(``starts_at``/``ends_at``) and admin-defined ``custom_fields`` (see
``app/services/custom_fields.py`` / ``app/models/field_definition.py``).

This is a brand-new table with no existing data, so the downgrade is a real,
safe drop.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OFFER_STATUS_VALUES = ("draft", "active", "expired", "archived")
OFFER_DISCOUNT_TYPE_VALUES = ("percentage", "fixed_amount")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*OFFER_STATUS_VALUES, name="offer_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*OFFER_DISCOUNT_TYPE_VALUES, name="offer_discount_type").create(
        bind, checkfirst=True
    )

    op.create_table(
        "offers",
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "discount_type",
            postgresql.ENUM(
                *OFFER_DISCOUNT_TYPE_VALUES, name="offer_discount_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("discount_value", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "applies_to", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*OFFER_STATUS_VALUES, name="offer_status", create_type=False),
            server_default="draft",
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
        sa.PrimaryKeyConstraint("id", name="pk_offers"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_offers_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_offers_business_id", "offers", ["business_id"])
    op.create_index(
        "ix_offers_business_id_status", "offers", ["business_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("offers")

    bind = op.get_bind()
    postgresql.ENUM(name="offer_discount_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="offer_status").drop(bind, checkfirst=True)
