"""Add services table for the Service catalog entity.

Introduces the ``service_status`` native Postgres enum type and the
``services`` table: a tenant-owned catalog entity mirroring ``products``
(name/description/price/currency/status/category), plus ``duration_minutes``
and a ``custom_fields`` JSONB column validated against
``field_definitions`` rows (entity_type='service') via
``app/services/custom_fields.py``.

This is a brand-new table with no existing data, so the downgrade is a real,
safe drop.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SERVICE_STATUS_VALUES = ("active", "inactive", "archived")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*SERVICE_STATUS_VALUES, name="service_status").create(
        bind, checkfirst=True
    )

    op.create_table(
        "services",
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*SERVICE_STATUS_VALUES, name="service_status", create_type=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_services"),
        sa.CheckConstraint("price >= 0", name="ck_services_price_non_negative"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_services_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_services_business_id", "services", ["business_id"])
    op.create_index(
        "ix_services_business_id_status", "services", ["business_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("services")

    bind = op.get_bind()
    postgresql.ENUM(name="service_status").drop(bind, checkfirst=True)
