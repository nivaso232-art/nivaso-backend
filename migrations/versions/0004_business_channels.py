"""Per-business channel credentials.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-01

``business_channels`` maps each business to its messaging channel credentials
(Telegram bot token, WhatsApp phone/token). Two unique constraints:
  - (business_id, channel_type): one channel per type per business
  - (channel_type, external_channel_id): one business per channel identity globally
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_channels",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 'whatsapp' | 'telegram'
        sa.Column("channel_type", sa.String(32), nullable=False),
        # For WhatsApp: phone_number_id. For Telegram: numeric bot id (prefix of token).
        sa.Column("external_channel_id", sa.String(128), nullable=False),
        # Sensitive credentials stored as JSONB (encrypted at rest by Supabase).
        # telegram:  {"bot_token": "...", "webhook_secret": "..."}
        # whatsapp:  {"phone_number_id": "...", "access_token": "...",
        #             "app_secret": "...", "verify_token": "..."}
        sa.Column("credentials", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            name="fk_business_channels_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_channels"),
        # One channel type per business
        sa.UniqueConstraint(
            "business_id", "channel_type",
            name="uq_business_channels_business_id_channel_type",
        ),
        # One business per channel identity (a WA number or TG bot can't serve two businesses)
        sa.UniqueConstraint(
            "channel_type", "external_channel_id",
            name="uq_business_channels_channel_type_external_channel_id",
        ),
    )
    op.create_index("ix_business_channels_business_id", "business_channels", ["business_id"])
    op.create_index(
        "ix_business_channels_channel_type_external",
        "business_channels",
        ["channel_type", "external_channel_id"],
    )

    op.execute("ALTER TABLE business_channels ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_channels FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE business_channels NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_channels DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_business_channels_channel_type_external", table_name="business_channels")
    op.drop_index("ix_business_channels_business_id", table_name="business_channels")
    op.drop_table("business_channels")
