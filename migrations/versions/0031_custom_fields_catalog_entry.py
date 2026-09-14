"""Add 'module.custom_fields' to the module catalog.

Custom field *management* (defining schemas for products/services/offers/
coupons) becomes its own requestable module, gated like everything else,
rather than an always-on default. See ``FeatureFlag.MODULE_CUSTOM_FIELDS``
and ``app/api/admin/custom_fields.py``.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO module_catalog (id, key, category, display_name, description, sort_order, is_active, created_at, updated_at)
            VALUES (gen_random_uuid(), 'module.custom_fields', 'module', 'Custom Fields', NULL, 8, true, now(), now())
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM module_catalog WHERE key = 'module.custom_fields'"))
