"""Business admin login credentials.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-03

Creates the business_admins table that stores one set of portal login
credentials per business. The password_hash column stores a bcrypt hash;
plaintext passwords are never persisted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_admins",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_business_admins_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_admins"),
        sa.UniqueConstraint("business_id", name="uq_business_admins_business_id"),
        sa.UniqueConstraint("username", name="uq_business_admins_username"),
    )
    op.create_index("ix_business_admins_username", "business_admins", ["username"])
    op.create_index("ix_business_admins_business_id", "business_admins", ["business_id"])


def downgrade() -> None:
    op.drop_table("business_admins")
