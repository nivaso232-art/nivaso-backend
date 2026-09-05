"""Add list_open_tickets and update_support_ticket to free/starter/pro plan defaults.

Revision ID: 0019
Revises: 0018

Root cause: these two tools were absent from ai.tools in all non-enterprise plans
even though support_tickets_enabled=True for those plans. Groq (and to a lesser
extent Anthropic) would reject or silently ignore model attempts to call those
tools, causing 400 errors and "Sorry, something went wrong" fallback replies.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MISSING_TOOLS = ["list_open_tickets", "update_support_ticket"]
_TARGET_PLANS = ["free", "starter", "pro"]


def upgrade() -> None:
    conn = op.get_bind()
    for plan in _TARGET_PLANS:
        row = conn.execute(
            sa.text("SELECT flags FROM plan_definitions WHERE plan_name = :plan"),
            {"plan": plan},
        ).fetchone()
        if row is None:
            continue

        flags: dict = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        tools: list | None = flags.get("ai.tools")
        if tools is None:
            # None = unrestricted (enterprise-style) — nothing to add
            continue

        changed = False
        for tool in _MISSING_TOOLS:
            if tool not in tools:
                # Insert after create_support_ticket for readability
                try:
                    idx = tools.index("create_support_ticket")
                    tools.insert(idx + 1, tool)
                except ValueError:
                    tools.append(tool)
                changed = True

        if changed:
            flags["ai.tools"] = tools
            conn.execute(
                sa.text(
                    "UPDATE plan_definitions SET flags = :flags, updated_by = 'migration-0019' "
                    "WHERE plan_name = :plan"
                ),
                {"flags": json.dumps(flags), "plan": plan},
            )


def downgrade() -> None:
    conn = op.get_bind()
    for plan in _TARGET_PLANS:
        row = conn.execute(
            sa.text("SELECT flags FROM plan_definitions WHERE plan_name = :plan"),
            {"plan": plan},
        ).fetchone()
        if row is None:
            continue

        flags: dict = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        tools: list | None = flags.get("ai.tools")
        if tools is None:
            continue

        original = [t for t in tools if t not in _MISSING_TOOLS]
        if original != tools:
            flags["ai.tools"] = original
            conn.execute(
                sa.text(
                    "UPDATE plan_definitions SET flags = :flags, updated_by = 'migration-0019-downgrade' "
                    "WHERE plan_name = :plan"
                ),
                {"flags": json.dumps(flags), "plan": plan},
            )
