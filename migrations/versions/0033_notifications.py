"""Add notifications table for lightweight in-app business notifications.

Introduces one new native Postgres enum type (``notification_severity``) and
the ``notifications`` table. First producer is the AI usage-limit check
(``app/services/ai_usage.py``): when a business crosses its super-admin-set
monthly spend cap, a row lands here (type ``ai_usage_limit_reached``) and the
admin portal surfaces it via ``GET /{slug}/notifications``.

This is a brand-new table with no existing data, so the downgrade is a real,
safe drop.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NOTIFICATION_SEVERITY_VALUES = ("info", "warning", "critical")


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*NOTIFICATION_SEVERITY_VALUES, name="notification_severity").create(
        bind, checkfirst=True
    )

    op.create_table(
        "notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            postgresql.ENUM(
                *NOTIFICATION_SEVERITY_VALUES, name="notification_severity", create_type=False
            ),
            server_default="info",
            nullable=False,
        ),
        sa.Column("is_read", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_notifications_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_notifications_business_id", "notifications", ["business_id"])
    op.create_index(
        "ix_notifications_business_id_is_read",
        "notifications",
        ["business_id", "is_read"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_business_id_is_read", table_name="notifications")
    op.drop_index("ix_notifications_business_id", table_name="notifications")
    op.drop_table("notifications")

    bind = op.get_bind()
    postgresql.ENUM(name="notification_severity").drop(bind, checkfirst=True)
