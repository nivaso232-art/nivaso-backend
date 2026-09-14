"""Add field_definitions table for admin-defined custom fields.

Introduces two new native Postgres enum types (``field_entity_type``,
``field_type``) and the ``field_definitions`` table: per-business, per-entity
schema definitions (key/label/type/options/required/sort_order) that
Product/Service/Offer/Coupon rows will validate their dynamic attributes
against (see ``app/services/custom_fields.py``).

This is a brand-new table with no existing data, so — unlike 0020 — the
downgrade is a real, safe drop.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FIELD_ENTITY_TYPE_VALUES = ("product", "service", "offer", "coupon")
FIELD_TYPE_VALUES = ("text", "number", "boolean", "select", "multiselect", "date")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*FIELD_ENTITY_TYPE_VALUES, name="field_entity_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*FIELD_TYPE_VALUES, name="field_type").create(bind, checkfirst=True)

    op.create_table(
        "field_definitions",
        sa.Column(
            "entity_type",
            postgresql.ENUM(*FIELD_ENTITY_TYPE_VALUES, name="field_entity_type", create_type=False),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column(
            "field_type",
            postgresql.ENUM(*FIELD_TYPE_VALUES, name="field_type", create_type=False),
            nullable=False,
        ),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_field_definitions"),
        sa.UniqueConstraint(
            "business_id", "entity_type", "key", name="uq_field_definitions_business_entity_key"
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_field_definitions_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_field_definitions_business_id", "field_definitions", ["business_id"]
    )
    op.create_index(
        "ix_field_definitions_business_entity",
        "field_definitions",
        ["business_id", "entity_type"],
    )


def downgrade() -> None:
    op.drop_table("field_definitions")

    bind = op.get_bind()
    postgresql.ENUM(name="field_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="field_entity_type").drop(bind, checkfirst=True)
