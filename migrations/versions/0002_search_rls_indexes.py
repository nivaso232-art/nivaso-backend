"""Search indexes and RLS deny-all.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-30

Everything Alembic cannot autogenerate:

* ``pg_trgm`` extension (needed by the fuzzy fallback in the search paths).
* GIN indexes on the generated ``tsvector`` columns - autogenerate does not
  emit opclass-specific index types.
* GIN trigram indexes on ``products.name`` and ``knowledge.title``.
* RLS enabled with **no policies** on every table.

On RLS: FastAPI connects as a privileged Postgres role and bypasses RLS, so
this changes nothing about how the app works today. It matters for what
happens *later* - the moment anyone points a Supabase ``anon`` or
``authenticated`` key at this database (an admin dashboard via supabase-js, a
quick script, a Studio query as a non-owner role), enabling RLS with no
policies means they get zero rows instead of everything. Deny-by-default is
free to add now and awkward to retrofit after a client is already reading data.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL_TABLES: tuple[str, ...] = (
    "businesses",
    "products",
    "customers",
    "customer_channels",
    "conversations",
    "messages",
    "orders",
    "order_items",
    "payments",
    "fulfillments",
    "knowledge",
    "support_tickets",
    "webhook_events",
    "agent_runs",
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    # pg_trgm powers similarity() in the search fallbacks. Supabase allows
    # this in the public schema; on a vanilla Postgres it needs superuser.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------
    # Full-text indexes
    # ------------------------------------------------------------------
    # GIN over the STORED generated tsvector columns. Without these, every
    # @@ match degrades to a sequential scan - fine at seed scale, fatal once
    # a tenant has a real catalog.
    op.execute(
        "CREATE INDEX ix_products_search_doc ON products USING gin (search_doc)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_search_doc ON knowledge USING gin (search_doc)"
    )

    # ------------------------------------------------------------------
    # Trigram indexes (the typo path)
    # ------------------------------------------------------------------
    # gin_trgm_ops indexes trigrams of the raw text, which is what makes
    # similarity() fast. "gt a5" -> "GTA 5" relies on this.
    op.execute(
        "CREATE INDEX ix_products_name_trgm "
        "ON products USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_title_trgm "
        "ON knowledge USING gin (title gin_trgm_ops)"
    )

    # ------------------------------------------------------------------
    # Row Level Security: enable, define no policies
    # ------------------------------------------------------------------
    for table in ALL_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE also applies RLS to the table's owner. Without it, the owner
        # role silently bypasses the policy set, which makes the deny-all
        # guarantee untestable from a Studio session.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # A commented reference implementation, not applied. Real tenant policies
    # need a claim to read business_id from, which only arrives once the admin
    # console uses Supabase Auth. Until then, deny-all is the honest state -
    # a policy written against a claim nobody sets would grant nothing while
    # looking like it grants something.
    #
    #   CREATE POLICY tenant_isolation ON products
    #     USING (business_id = (auth.jwt() -> 'app_metadata' ->> 'business_id')::uuid);


def downgrade() -> None:
    for table in ALL_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP INDEX IF EXISTS ix_knowledge_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_products_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_search_doc")
    op.execute("DROP INDEX IF EXISTS ix_products_search_doc")

    # pg_trgm is deliberately NOT dropped. Other schemas or extensions in the
    # same database may depend on it, and dropping a shared extension during a
    # routine downgrade is a bigger blast radius than leaving it installed.
