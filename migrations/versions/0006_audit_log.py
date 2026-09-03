"""Entitlement audit log.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-02

Records every super-admin action that changes a business's plan, overrides,
status, or feature request outcome. Append-only — no updates or deletes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entitlement_audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("performed_by", sa.Text(), nullable=False, server_default="super-admin"),
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
        sa.PrimaryKeyConstraint("id", name="pk_entitlement_audit_logs"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_entitlement_audit_logs_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_entitlement_audit_logs_business_id",
        "entitlement_audit_logs",
        ["business_id"],
    )
    op.create_index(
        "ix_entitlement_audit_logs_action",
        "entitlement_audit_logs",
        ["action"],
    )
    op.create_index(
        "ix_entitlement_audit_logs_created_at",
        "entitlement_audit_logs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("entitlement_audit_logs")
