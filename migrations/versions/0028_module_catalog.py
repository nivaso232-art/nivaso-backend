"""Add module_catalog table: the extensible, admin-visible catalog of
requestable modules and integrations.

Replaces the hardcoded ``REQUESTABLE_MODULE_FLAGS`` frozenset that used to
gate ``POST /auth/signup``'s ``requested_modules`` field. Seeds the 8 modules
and 5 integrations that exist today; a future integration only needs a new
row here, not new code.

This is a brand-new table with no existing data, so the downgrade is a real,
safe drop.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MODULE_CATALOG_CATEGORY_VALUES = (
    "module",
    "integration",
)

SEED_ROWS = [
    {"key": "module.products", "category": "module", "display_name": "Products", "sort_order": 0},
    {"key": "module.services", "category": "module", "display_name": "Services", "sort_order": 1},
    {"key": "module.offers", "category": "module", "display_name": "Offers", "sort_order": 2},
    {"key": "module.coupons", "category": "module", "display_name": "Coupons", "sort_order": 3},
    {"key": "orders.enabled", "category": "module", "display_name": "Orders", "sort_order": 4},
    {"key": "module.appointments", "category": "module", "display_name": "Appointments", "sort_order": 5},
    {"key": "module.customers", "category": "module", "display_name": "Customers", "sort_order": 6},
    {"key": "support.tickets_enabled", "category": "module", "display_name": "Support Tickets", "sort_order": 7},
    {"key": "channel.web", "category": "integration", "display_name": "Web Chat", "sort_order": 0},
    {"key": "channel.whatsapp", "category": "integration", "display_name": "WhatsApp", "sort_order": 1},
    {"key": "channel.telegram", "category": "integration", "display_name": "Telegram", "sort_order": 2},
    {"key": "channel.instagram", "category": "integration", "display_name": "Instagram", "sort_order": 3},
    {"key": "channel.payments", "category": "integration", "display_name": "Razorpay Payments", "sort_order": 4},
]


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*MODULE_CATALOG_CATEGORY_VALUES, name="module_catalog_category").create(
        bind, checkfirst=True
    )

    op.create_table(
        "module_catalog",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(
                *MODULE_CATALOG_CATEGORY_VALUES, name="module_catalog_category", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_module_catalog"),
        sa.UniqueConstraint("key", name="uq_module_catalog_key"),
    )
    op.create_index("ix_module_catalog_key", "module_catalog", ["key"], unique=True)

    module_catalog = sa.table(
        "module_catalog",
        sa.column("key", sa.String),
        sa.column(
            "category",
            postgresql.ENUM(
                *MODULE_CATALOG_CATEGORY_VALUES, name="module_catalog_category", create_type=False
            ),
        ),
        sa.column("display_name", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(module_catalog, SEED_ROWS)


def downgrade() -> None:
    op.drop_index("ix_module_catalog_key", table_name="module_catalog")
    op.drop_table("module_catalog")

    bind = op.get_bind()
    postgresql.ENUM(name="module_catalog_category").drop(bind, checkfirst=True)
