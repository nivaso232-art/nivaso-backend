"""Sync plan_definitions ai.tools with expanded tool catalog.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-03

Updates the ``ai.tools`` flag in the ``plan_definitions`` table so that the
super-admin Plan Defaults UI reflects the full expanded tool catalog:

  free     → 10 tools (all original tools, no longer restricted)
  starter  → 13 tools (+ list_my_orders, get_full_article, compare_products)
  pro      → 17 tools (+ check_product_availability, get_fulfillment_details,
                          get_order_payment_history, retry_payment)
  enterprise → null (unrestricted — all current and future tools)

Uses INSERT … ON CONFLICT DO UPDATE so the migration is safe regardless of
whether a plan has an existing row in plan_definitions.  Only the ``ai.tools``
key is touched; other flags in the row are preserved via JSONB merge.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FREE_TOOLS = [
    "search_products",
    "get_product",
    "create_order",
    "get_order_status",
    "cancel_order",
    "create_payment_link",
    "check_payment_status",
    "search_knowledge",
    "create_support_ticket",
    "get_my_credentials",
]

_STARTER_TOOLS = _FREE_TOOLS + [
    "compare_products",
    "list_my_orders",
    "get_full_article",
]

_PRO_TOOLS = _STARTER_TOOLS + [
    "check_product_availability",
    "get_fulfillment_details",
    "get_order_payment_history",
    "retry_payment",
]

_NEW_AI_TOOLS: dict[str, list | None] = {
    "free":       _FREE_TOOLS,
    "starter":    _STARTER_TOOLS,
    "pro":        _PRO_TOOLS,
    "enterprise": None,   # null = unrestricted
}

# Old values used in downgrade (pre-expansion, original gated tool lists)
_OLD_AI_TOOLS: dict[str, list | None] = {
    "free":       ["search_products", "get_product", "search_knowledge", "create_support_ticket"],
    "starter":    ["search_products", "get_product", "create_order", "get_order_status",
                   "search_knowledge", "create_support_ticket"],
    "pro":        ["search_products", "get_product", "create_order", "get_order_status",
                   "cancel_order", "create_payment_link", "check_payment_status",
                   "search_knowledge", "create_support_ticket"],
    "enterprise": None,
}


def upgrade() -> None:
    conn = op.get_bind()
    for plan_name, tools in _NEW_AI_TOOLS.items():
        patch = json.dumps({"ai.tools": tools})
        conn.execute(
            sa.text(
                "INSERT INTO plan_definitions (plan_name, flags, updated_by) "
                "VALUES (:pn, cast(:patch as jsonb), 'migration-0011') "
                "ON CONFLICT (plan_name) DO UPDATE "
                "SET flags = plan_definitions.flags || cast(:patch as jsonb), "
                "    updated_by = 'migration-0011'"
            ),
            {"pn": plan_name, "patch": patch},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for plan_name, tools in _OLD_AI_TOOLS.items():
        patch = json.dumps({"ai.tools": tools})
        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = flags || cast(:patch as jsonb), "
                "    updated_by = 'migration-0011-downgrade' "
                "WHERE plan_name = :pn"
            ),
            {"pn": plan_name, "patch": patch},
        )
