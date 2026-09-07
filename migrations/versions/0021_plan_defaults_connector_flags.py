"""Add AI_ENABLED and connector automation flags to plan_definitions.

Revision ID: 0021
Revises: 0020

Adds to all plans:
  - ai.enabled: True

Adds to free/starter plans (no automation access):
  - connector.whatsapp_automation: False
  - connector.telegram_automation: False
  - connector.web_automation: False

Adds to pro/enterprise plans (full automation access):
  - connector.whatsapp_automation: True
  - connector.telegram_automation: True
  - connector.web_automation: True
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# New flags that every plan gets
_AI_ENABLED_FLAG = {"ai.enabled": True}

# Connector automation flags by plan tier
_AUTOMATION_FLAGS_RESTRICTED = {
    "connector.whatsapp_automation": False,
    "connector.telegram_automation": False,
    "connector.web_automation": False,
}
_AUTOMATION_FLAGS_ENABLED = {
    "connector.whatsapp_automation": True,
    "connector.telegram_automation": True,
    "connector.web_automation": True,
}

_PLAN_AUTOMATION_FLAGS = {
    "free": _AUTOMATION_FLAGS_RESTRICTED,
    "starter": _AUTOMATION_FLAGS_RESTRICTED,
    "pro": _AUTOMATION_FLAGS_ENABLED,
    "enterprise": _AUTOMATION_FLAGS_ENABLED,
}


def upgrade() -> None:
    conn = op.get_bind()
    for plan in ("free", "starter", "pro", "enterprise"):
        row = conn.execute(
            sa.text("SELECT flags FROM plan_definitions WHERE plan_name = :plan"),
            {"plan": plan},
        ).fetchone()
        if row is None:
            continue

        flags: dict = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        # Add ai.enabled
        flags.update(_AI_ENABLED_FLAG)

        # Add connector automation flags
        flags.update(_PLAN_AUTOMATION_FLAGS[plan])

        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = :flags, updated_by = 'migration-0021' "
                "WHERE plan_name = :plan"
            ),
            {"flags": json.dumps(flags), "plan": plan},
        )


def downgrade() -> None:
    _keys_to_remove = {
        "ai.enabled",
        "connector.whatsapp_automation",
        "connector.telegram_automation",
        "connector.web_automation",
    }
    conn = op.get_bind()
    for plan in ("free", "starter", "pro", "enterprise"):
        row = conn.execute(
            sa.text("SELECT flags FROM plan_definitions WHERE plan_name = :plan"),
            {"plan": plan},
        ).fetchone()
        if row is None:
            continue

        flags: dict = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        for key in _keys_to_remove:
            flags.pop(key, None)

        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = :flags, updated_by = 'migration-0021-downgrade' "
                "WHERE plan_name = :plan"
            ),
            {"flags": json.dumps(flags), "plan": plan},
        )
