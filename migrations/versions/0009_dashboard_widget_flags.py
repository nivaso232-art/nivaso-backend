"""Dashboard widget customization flags.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-03

Retires ``ui.analytics`` (the dashboard is no longer an all-or-nothing gate —
a basic set of widgets always shows) and adds two new flags:
``ui.dashboard_customize`` and ``ui.dashboard_widgets``. See
app/entitlements/flags.py for the authoritative definitions; this migration
just brings the DB-stored plan_definitions rows in line with them so the
super-admin Plan Defaults editor doesn't show a stale flag.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_FLAGS: dict[str, dict] = {
    "free": {"ui.dashboard_customize": False, "ui.dashboard_widgets": []},
    "starter": {"ui.dashboard_customize": False, "ui.dashboard_widgets": []},
    "pro": {
        "ui.dashboard_customize": True,
        "ui.dashboard_widgets": [
            "stat.active_sessions",
            "stat.agent_runs_today",
            "stat.published_articles",
            "chart.agent_runs_7d",
            "chart.ticket_status",
        ],
    },
    "enterprise": {"ui.dashboard_customize": True, "ui.dashboard_widgets": None},
}

_OLD_ANALYTICS: dict[str, bool] = {
    "free": False,
    "starter": True,
    "pro": True,
    "enterprise": True,
}


def upgrade() -> None:
    conn = op.get_bind()
    for plan_name, new_flags in _NEW_FLAGS.items():
        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = (flags - 'ui.analytics') || cast(:new_flags as jsonb) "
                "WHERE plan_name = :pn"
            ),
            {"pn": plan_name, "new_flags": json.dumps(new_flags)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for plan_name, had_analytics in _OLD_ANALYTICS.items():
        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = (flags - 'ui.dashboard_customize' - 'ui.dashboard_widgets') "
                "  || cast(:old_flags as jsonb) "
                "WHERE plan_name = :pn"
            ),
            {"pn": plan_name, "old_flags": json.dumps({"ui.analytics": had_analytics})},
        )
