"""Add Groq models to Pro plan ai.models in plan_definitions.

Revision ID: 0015
Revises: 0014

Groq provides fast OpenAI-compatible inference. The following models are added
to the Pro plan (Enterprise already has ai.models=null, so it's unrestricted):
  openai/gpt-oss-120b
  llama-3.3-70b-versatile
  llama3-groq-70b-8192-tool-use-preview
  deepseek-r1-distill-llama-70b
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-groq-70b-8192-tool-use-preview",
    "openai/gpt-oss-120b",
    "deepseek-r1-distill-llama-70b",
]


def upgrade() -> None:
    conn = op.get_bind()
    for model in _GROQ_MODELS:
        # Append only if not already present to stay idempotent.
        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = jsonb_set("
                "  flags,"
                "  '{ai.models}',"
                "  (flags->'ai.models') || cast(:model as jsonb)"
                ") "
                "WHERE plan_name = 'pro' "
                "AND flags->'ai.models' IS NOT NULL "
                "AND NOT (flags->'ai.models' @> cast(:model as jsonb))"
            ),
            {"model": json.dumps([model])},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for model in _GROQ_MODELS:
        conn.execute(
            sa.text(
                "UPDATE plan_definitions "
                "SET flags = jsonb_set("
                "  flags,"
                "  '{ai.models}',"
                "  ("
                "    SELECT jsonb_agg(v) FROM jsonb_array_elements(flags->'ai.models') v "
                f"   WHERE v::text != '\"{ model }\"'"
                "  )"
                ") "
                "WHERE plan_name = 'pro' "
                "AND flags->'ai.models' @> cast(:model as jsonb)"
            ),
            {"model": json.dumps([model])},
        )
