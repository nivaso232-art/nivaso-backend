"""Replace deprecated gemini-2.0-flash-lite with gemini-3.5-flash-lite.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-03

Google deprecated the gemini-2.0-flash-lite model. This migration scans
the ai.models arrays stored in plan_definitions and business_entitlements
and replaces any occurrence with the recommended replacement.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _swap(table: str, col: str, old: str, new: str) -> str:
    """Return SQL that replaces one value in a JSONB string array column."""
    return (
        f"UPDATE {table} "
        f"SET {col} = jsonb_set("
        f"  {col},"
        f"  '{{ai.models}}',"
        f"  ("
        f"    SELECT jsonb_agg("
        f"      CASE WHEN elem = '\"{old}\"'::jsonb"
        f"           THEN '\"{new}\"'::jsonb"
        f"           ELSE elem END"
        f"    )"
        f"    FROM jsonb_array_elements({col}->'ai.models') elem"
        f"  )"
        f") "
        f"WHERE {col}->'ai.models' @> '[\"{old}\"]'::jsonb"
    )


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(_swap("plan_definitions", "flags",
                               "gemini-2.0-flash-lite", "gemini-3.5-flash-lite")))
    conn.execute(sa.text(_swap("business_entitlements", "overrides",
                               "gemini-2.0-flash-lite", "gemini-3.5-flash-lite")))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(_swap("plan_definitions", "flags",
                               "gemini-3.5-flash-lite", "gemini-2.0-flash-lite")))
    conn.execute(sa.text(_swap("business_entitlements", "overrides",
                               "gemini-3.5-flash-lite", "gemini-2.0-flash-lite")))
