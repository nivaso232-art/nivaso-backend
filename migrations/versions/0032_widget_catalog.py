"""Add 'widget' category to module_catalog and seed the 15 gated dashboard
widget keys.

Per-widget dashboard access moves from a plan-list gate
(``FeatureFlag.UI_DASHBOARD_WIDGETS``) to the same per-business-grantable
module-catalog/entitlement-request system already used for ``module.*`` and
``channel.*`` flags (see ``app/entitlements/dashboard_widgets.py``). A gated
widget becomes just another catalog row — automatically requestable and
reviewable with zero new request-flow code.

The 7 default/basic widget keys (``stat.products``, ``stat.customers``,
``stat.open_tickets``, ``stat.products_delivered``, ``chart.revenue``,
``list.needs_attention``, ``gauge.plan_usage``) are NOT seeded here — they
remain always-on via ``DASHBOARD_BASIC_WIDGET_KEYS`` and never need a request.

``ADD VALUE`` cannot run inside a transaction, so we commit the migration's
implicit transaction first, then issue the ALTER with ``IF NOT EXISTS``
(mirrors ``0020_instagram_channel.py``).

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_ROWS = [
    {"key": "stat.active_sessions", "display_name": "Active Sessions", "sort_order": 0},
    {"key": "stat.agent_runs_today", "display_name": "Agent Runs Today", "sort_order": 1},
    {"key": "stat.published_articles", "display_name": "Published Articles", "sort_order": 2},
    {"key": "chart.agent_runs_7d", "display_name": "Agent Runs (7-day)", "sort_order": 3},
    {"key": "chart.ticket_status", "display_name": "Ticket Status Breakdown", "sort_order": 4},
    {"key": "chart.product_catalog", "display_name": "Product Catalog Breakdown", "sort_order": 5},
    {"key": "chart.token_usage", "display_name": "AI Token Usage (7-day)", "sort_order": 6},
    {"key": "chart.ticket_priority", "display_name": "Open Ticket Priority", "sort_order": 7},
    {"key": "stat.orders_today", "display_name": "Orders Today", "sort_order": 8},
    {"key": "donut.order_status", "display_name": "Order Status Breakdown", "sort_order": 9},
    {"key": "bar.top_products", "display_name": "Top Selling Products", "sort_order": 10},
    {"key": "funnel.sales", "display_name": "Sales Funnel", "sort_order": 11},
    {"key": "stat.active_coupons", "display_name": "Active Coupons", "sort_order": 12},
    {"key": "stat.active_offers", "display_name": "Active Offers", "sort_order": 13},
    {"key": "stat.appointments_upcoming", "display_name": "Upcoming Appointments", "sort_order": 14},
]


def upgrade() -> None:
    # End the transaction alembic opened so ADD VALUE can run standalone.
    op.execute("COMMIT")
    op.execute("ALTER TYPE module_catalog_category ADD VALUE IF NOT EXISTS 'widget'")

    # Enum-add and insert are two separate steps: a newly added enum value
    # cannot be used in the same transaction that added it on older Postgres.
    for row in SEED_ROWS:
        op.execute(
            sa.text(
                """
                INSERT INTO module_catalog (id, key, category, display_name, description, sort_order, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), :key, 'widget', :display_name, NULL, :sort_order, true, now(), now())
                ON CONFLICT (key) DO NOTHING
                """
            ).bindparams(key=row["key"], display_name=row["display_name"], sort_order=row["sort_order"])
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM module_catalog WHERE category = 'widget'"))
    # Postgres cannot drop a single enum value without rebuilding the type —
    # left in place, same as every other enum-add migration in this repo.
