"""Unified dashboard widget catalog.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-03

Expands ``ui.dashboard_widgets`` in plan_definitions to include the five
formerly-hardcoded basic widgets (stat.products, stat.customers,
stat.open_tickets, stat.products_delivered, chart.revenue). Free/Starter
plans now have these basics explicitly rather than relying on a hardcoded
always-on frontend tier. Pro gets them prepended to its existing advanced
list. Enterprise is already unrestricted (null) and needs no change.

Widget dependency enforcement is a code-only change (see flags.py and
dashboard.py) — no schema migration is needed for that.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASICS = [
    "stat.products",
    "stat.customers",
    "stat.open_tickets",
    "stat.products_delivered",
    "chart.revenue",
]

_NEW_WIDGETS: dict[str, list | None] = {
    "free":       _BASICS,
    "starter":    _BASICS,
    "pro": _BASICS + [
        "stat.active_sessions",
        "stat.agent_runs_today",
        "stat.published_articles",
        "chart.agent_runs_7d",
        "chart.ticket_status",
    ],
    # enterprise: None (unrestricted) — no change
}

_OLD_WIDGETS: dict[str, list | None] = {
    "free":       [],
    "starter":    [],
    "pro": [
        "stat.active_sessions",
        "stat.agent_runs_today",
        "stat.published_articles",
        "chart.agent_runs_7d",
        "chart.ticket_status",
    ],
}


def upgrade() -> None:
    conn = op.get_bind()
    for plan_name, widgets in _NEW_WIDGETS.items():
        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = flags || cast(:patch as jsonb) "
                "WHERE plan_name = :pn"
            ),
            {"pn": plan_name, "patch": json.dumps({"ui.dashboard_widgets": widgets})},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for plan_name, widgets in _OLD_WIDGETS.items():
        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = flags || cast(:patch as jsonb) "
                "WHERE plan_name = :pn"
            ),
            {"pn": plan_name, "patch": json.dumps({"ui.dashboard_widgets": widgets})},
        )
