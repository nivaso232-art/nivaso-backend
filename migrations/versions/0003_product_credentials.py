"""Reusable credential vault.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01

Adds ``product_credentials`` — the encrypted pool of reusable game accounts —
plus its ``credential_status`` enum. RLS is enabled deny-all to match every
other table (see 0002).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = ("active", "exhausted", "disabled")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*_STATUS, name="credential_status").create(bind, checkfirst=True)

    op.create_table(
        "product_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("allocated", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*_STATUS, name="credential_status", create_type=False),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default="{}",
            nullable=False,
        ),
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
        sa.CheckConstraint("capacity > 0", name="capacity_positive"),
        sa.CheckConstraint("allocated >= 0", name="allocated_non_negative"),
        sa.CheckConstraint("allocated <= capacity", name="allocated_within_capacity"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_product_credentials_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_credentials_product_id_products",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_credentials"),
    )
    op.create_index(
        "ix_product_credentials_business_id", "product_credentials", ["business_id"]
    )
    op.create_index(
        "ix_product_credentials_business_id_product_id_status",
        "product_credentials",
        ["business_id", "product_id", "status"],
    )

    op.execute("ALTER TABLE product_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE product_credentials FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE product_credentials NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE product_credentials DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_product_credentials_business_id_product_id_status",
        table_name="product_credentials",
    )
    op.drop_index("ix_product_credentials_business_id", table_name="product_credentials")
    op.drop_table("product_credentials")
    postgresql.ENUM(name="credential_status").drop(op.get_bind(), checkfirst=True)
