"""Add request_feature_access and check_feature_request_status to all plan ai.tools.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-03

These tools are available on every plan with no feature-flag dependency gate.
They allow the AI agent to submit and check feature-access requests when a
customer needs a capability that is not yet enabled on the business's plan.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TOOLS = ["request_feature_access", "check_feature_request_status"]


def _append_to_ai_tools(plan_name: str, tools: list[str]) -> None:
    """Append tools to plan_definitions.flags['ai.tools'] if not already present."""
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT flags FROM plan_definitions WHERE plan_name = :pn"),
        {"pn": plan_name},
    ).fetchone()

    if row is None:
        return  # No DB override for this plan — code defaults handle it

    flags = dict(row[0]) if row[0] else {}
    current_tools = flags.get("ai.tools")

    if current_tools is None:
        return  # null = unrestricted (enterprise) — no change needed

    updated = list(current_tools)
    changed = False
    for tool in tools:
        if tool not in updated:
            updated.append(tool)
            changed = True

    if changed:
        flags["ai.tools"] = updated
        conn.execute(
            sa.text(
                "UPDATE plan_definitions SET flags = cast(:flags as jsonb) "
                "WHERE plan_name = :pn"
            ),
            {"pn": plan_name, "flags": json.dumps(flags)},
        )


def upgrade() -> None:
    for plan in ("free", "starter", "pro"):
        _append_to_ai_tools(plan, _NEW_TOOLS)


def downgrade() -> None:
    conn = op.get_bind()
    for plan in ("free", "starter", "pro"):
        for tool in _NEW_TOOLS:
            conn.execute(
                sa.text(
                    "UPDATE plan_definitions "
                    "SET flags = jsonb_set(flags, '{ai.tools}', "
                    "  (SELECT jsonb_agg(v) FROM jsonb_array_elements(flags->'ai.tools') v "
                    f"   WHERE v::text != '\"{ tool }\"')) "
                    "WHERE plan_name = :pn AND flags->'ai.tools' IS NOT NULL"
                ),
                {"pn": plan},
            )
