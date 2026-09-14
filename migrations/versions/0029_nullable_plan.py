"""Make business_entitlements.plan nullable, drop its 'free' default.

Plans become an optional, super-admin-assigned convenience rather than an
automatic default: a new business now gets no plan at all (NULL), and every
flag resolves purely from ``overrides`` until a plan is explicitly assigned
or a module is explicitly approved/toggled on. See
app/entitlements/resolver.py::resolve for the NULL-plan resolution rule.

Existing rows keep whatever plan they already have — this only changes the
column's constraints and default for future inserts, it does not touch data.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "business_entitlements",
        "plan",
        existing_type=sa.String(32),
        nullable=True,
        server_default=None,
    )


def downgrade() -> None:
    # Backfill NULLs to 'free' before re-adding NOT NULL, or the constraint
    # would fail against any business that signed up under the new rule.
    op.execute("UPDATE business_entitlements SET plan = 'free' WHERE plan IS NULL")
    op.alter_column(
        "business_entitlements",
        "plan",
        existing_type=sa.String(32),
        nullable=False,
        server_default="free",
    )
