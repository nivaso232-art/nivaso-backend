"""Add module_requests table for elective catalog module approval requests.

During signup a business owner may select elective catalog modules
(Services/Offers/Coupons/Appointments) they want. Rather than granting them
automatically, each selection becomes a ``module_requests`` row that a
super-admin must individually review and approve or deny — separate from the
whole-business pending-approval gate on ``businesses.status``.

Introduces one new native Postgres enum type (``module_request_status``) and
the ``module_requests`` table: one row per (business, module_key), unique on
that pair so re-requesting the same module is a no-op rather than a duplicate
row.

This is a brand-new table with no existing data, so the downgrade is a real,
safe drop.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MODULE_REQUEST_STATUS_VALUES = (
    "pending",
    "approved",
    "denied",
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*MODULE_REQUEST_STATUS_VALUES, name="module_request_status").create(
        bind, checkfirst=True
    )

    op.create_table(
        "module_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module_key", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *MODULE_REQUEST_STATUS_VALUES, name="module_request_status", create_type=False
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_module_requests"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_module_requests_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "business_id", "module_key", name="uq_module_requests_business_id_module_key"
        ),
    )
    op.create_index("ix_module_requests_business_id", "module_requests", ["business_id"])
    op.create_index("ix_module_requests_status", "module_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_module_requests_status", table_name="module_requests")
    op.drop_index("ix_module_requests_business_id", table_name="module_requests")
    op.drop_table("module_requests")

    bind = op.get_bind()
    postgresql.ENUM(name="module_request_status").drop(bind, checkfirst=True)
