"""Plan definitions — editable plan tier catalogue.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-02

Creates the plan_definitions table and seeds it with the four default tiers
(free, starter, pro, enterprise) from the code-level PLAN_DEFAULTS so that
existing behaviour is preserved after the migration runs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Snapshot of PLAN_DEFAULTS at migration time.  Keep in sync with
# app/entitlements/flags.py — the table is the live source of truth after this.
_SEED: dict[str, dict] = {
    "free": {
        "ai.models": ["claude-haiku-4-5-20251001"],
        "ai.custom_model_picker": False,
        "ai.max_iterations": 3,
        "ai.tools": [
            "search_products",
            "get_product",
            "search_knowledge",
            "create_support_ticket",
        ],
        "channel.web": True,
        "channel.whatsapp": False,
        "channel.telegram": False,
        "channel.payments": False,
        "catalog.products_limit": 25,
        "knowledge.articles_limit": 5,
        "orders.enabled": False,
        "support.tickets_enabled": True,
        "credentials.enabled": False,
        "ui.analytics": False,
        "ui.agent_runs": False,
        "ui.webhook_events": False,
    },
    "starter": {
        "ai.models": ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
        "ai.custom_model_picker": False,
        "ai.max_iterations": 5,
        "ai.tools": [
            "search_products",
            "get_product",
            "create_order",
            "get_order_status",
            "search_knowledge",
            "create_support_ticket",
        ],
        "channel.web": True,
        "channel.whatsapp": False,
        "channel.telegram": False,
        "channel.payments": False,
        "catalog.products_limit": 100,
        "knowledge.articles_limit": 20,
        "orders.enabled": True,
        "support.tickets_enabled": True,
        "credentials.enabled": False,
        "ui.analytics": True,
        "ui.agent_runs": False,
        "ui.webhook_events": False,
    },
    "pro": {
        "ai.models": [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-opus-4-7",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
        ],
        "ai.custom_model_picker": True,
        "ai.max_iterations": 8,
        "ai.tools": [
            "search_products",
            "get_product",
            "create_order",
            "get_order_status",
            "cancel_order",
            "create_payment_link",
            "check_payment_status",
            "search_knowledge",
            "create_support_ticket",
        ],
        "channel.web": True,
        "channel.whatsapp": True,
        "channel.telegram": True,
        "channel.payments": True,
        "catalog.products_limit": 1000,
        "knowledge.articles_limit": 100,
        "orders.enabled": True,
        "support.tickets_enabled": True,
        "credentials.enabled": False,
        "ui.analytics": True,
        "ui.agent_runs": True,
        "ui.webhook_events": True,
    },
    "enterprise": {
        "ai.models": None,
        "ai.custom_model_picker": True,
        "ai.max_iterations": None,
        "ai.tools": None,
        "channel.web": True,
        "channel.whatsapp": True,
        "channel.telegram": True,
        "channel.payments": True,
        "catalog.products_limit": None,
        "knowledge.articles_limit": None,
        "orders.enabled": True,
        "support.tickets_enabled": True,
        "credentials.enabled": True,
        "ui.analytics": True,
        "ui.agent_runs": True,
        "ui.webhook_events": True,
    },
}


def upgrade() -> None:
    op.create_table(
        "plan_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("plan_name", sa.String(32), nullable=False),
        sa.Column(
            "flags",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_plan_definitions"),
        sa.UniqueConstraint("plan_name", name="uq_plan_definitions_plan_name"),
    )
    op.create_index(
        "ix_plan_definitions_plan_name",
        "plan_definitions",
        ["plan_name"],
    )

    conn = op.get_bind()
    for plan_name, flags in _SEED.items():
        conn.execute(
            sa.text(
                "INSERT INTO plan_definitions (plan_name, flags, updated_by) "
                "VALUES (:pn, cast(:fl as jsonb), 'seed')"
            ),
            {"pn": plan_name, "fl": json.dumps(flags)},
        )


def downgrade() -> None:
    op.drop_table("plan_definitions")
