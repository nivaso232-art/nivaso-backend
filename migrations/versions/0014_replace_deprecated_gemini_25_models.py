"""Replace deprecated Gemini 2.5 models with current equivalents.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-04

Google deprecated gemini-2.5-pro and gemini-2.5-flash for new users.
  gemini-2.5-pro   → gemini-3.1-pro-preview
  gemini-2.5-flash → gemini-3.6-flash

Scans ai.models arrays in plan_definitions and business_entitlements.overrides.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REPLACEMENTS = [
    ("gemini-2.5-pro",   "gemini-3.1-pro-preview"),
    ("gemini-2.5-flash", "gemini-3.6-flash"),
]


def _swap(table: str, col: str, old: str, new: str) -> str:
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
    for old, new in _REPLACEMENTS:
        conn.execute(sa.text(_swap("plan_definitions",        "flags",    old, new)))
        conn.execute(sa.text(_swap("business_entitlements",   "overrides", old, new)))


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in _REPLACEMENTS:
        conn.execute(sa.text(_swap("plan_definitions",        "flags",    new, old)))
        conn.execute(sa.text(_swap("business_entitlements",   "overrides", new, old)))
