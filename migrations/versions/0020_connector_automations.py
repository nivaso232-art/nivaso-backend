"""Connector automation flows per business.

Revision ID: 0020
Revises: 0019

``connector_automations`` stores the button/menu workflow tree for each
messaging channel. One row per (business, connector_type) pair.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_FLOW = (
    '{"welcome_message":"","fallback_message":'
    '"Sorry, I could not understand. Please choose an option.","nodes":[]}'
)


def upgrade() -> None:
    op.create_table(
        "connector_automations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 'whatsapp' | 'telegram' | 'web'
        sa.Column("connector_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default="Default Flow"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "flow",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_FLOW}'::jsonb"),
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
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_connector_automations_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connector_automations"),
        sa.UniqueConstraint(
            "business_id",
            "connector_type",
            name="uq_connector_automations_business_id_connector_type",
        ),
    )
    op.create_index(
        "ix_connector_automations_business_id",
        "connector_automations",
        ["business_id"],
    )
    op.create_index(
        "ix_connector_automations_business_id_connector_type",
        "connector_automations",
        ["business_id", "connector_type"],
    )

    op.execute("ALTER TABLE connector_automations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector_automations FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE connector_automations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector_automations DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_connector_automations_business_id_connector_type",
        table_name="connector_automations",
    )
    op.drop_index(
        "ix_connector_automations_business_id",
        table_name="connector_automations",
    )
    op.drop_table("connector_automations")
