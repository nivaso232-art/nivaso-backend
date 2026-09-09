"""Add 'instagram' to the channel and webhook_source enums.

Instagram DMs reuse the existing customer/conversation/webhook_event pipeline,
so the only schema change needed is a new value on two native PG enums:

  * ``channel``        — used by conversations.channel, customer_channels.channel
  * ``webhook_source`` — used by webhook_events.source

``ADD VALUE`` cannot run inside a transaction on older Postgres, so we commit
the migration's implicit transaction first, then issue the ALTERs with
``IF NOT EXISTS`` (idempotent — safe if already applied out-of-band).

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # End the transaction alembic opened so ADD VALUE can run standalone.
    op.execute("COMMIT")
    op.execute("ALTER TYPE channel ADD VALUE IF NOT EXISTS 'instagram'")
    op.execute("ALTER TYPE webhook_source ADD VALUE IF NOT EXISTS 'instagram'")


def downgrade() -> None:
    # Postgres cannot drop a single enum value without rebuilding the type.
    # Instagram rows would have to be migrated off first; left as a no-op.
    pass
