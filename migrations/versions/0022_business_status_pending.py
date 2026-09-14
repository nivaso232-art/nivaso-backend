"""Add 'pending' to the business_status enum.

A newly signed-up business sits in PENDING until a super-admin approves it -
it cannot log in until then (see app/api/auth.py::business_login). The only
schema change needed is a new value on the existing ``business_status`` PG
enum.

``ADD VALUE`` cannot run inside a transaction on older Postgres, so we commit
the migration's implicit transaction first, then issue the ALTER with
``IF NOT EXISTS`` (idempotent - safe if already applied out-of-band).

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # End the transaction alembic opened so ADD VALUE can run standalone.
    op.execute("COMMIT")
    op.execute("ALTER TYPE business_status ADD VALUE IF NOT EXISTS 'pending'")


def downgrade() -> None:
    # Postgres cannot drop a single enum value without rebuilding the type.
    # Pending businesses would have to be migrated off first; left as a no-op.
    pass
