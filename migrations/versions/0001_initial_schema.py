"""Initial schema: 14 tables, 16 native enum types.

Revision ID: 0001
Revises:
Create Date: 2026-08-30

Enum types are created up front, once, rather than inline in ``create_table``.
Alembic's inline path issues ``CREATE TYPE`` per column, which breaks for any
enum used by more than one table (``channel`` is used by both
``customer_channels`` and ``conversations``) - the second table would fail with
"type already exists". Creating them explicitly and passing
``create_type=False`` everywhere makes the ordering explicit and the downgrade
symmetric.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --------------------------------------------------------------------------
# Enum types
# --------------------------------------------------------------------------
ENUMS: dict[str, tuple[str, ...]] = {
    "business_status": ("active", "suspended", "inactive"),
    "product_status": ("active", "inactive", "out_of_stock", "archived"),
    "channel": ("whatsapp", "telegram", "web"),
    "conversation_status": ("active", "closed"),
    "sender_type": ("customer", "assistant", "tool", "system", "agent"),
    "message_type": (
        "text",
        "image",
        "audio",
        "video",
        "document",
        "location",
        "tool_call",
        "tool_result",
        "system_note",
    ),
    "message_status": (
        "received",
        "pending",
        "sent",
        "delivered",
        "read",
        "failed",
    ),
    "order_status": (
        "DRAFT",
        "PENDING_CONFIRMATION",
        "PAYMENT_PENDING",
        "PAYMENT_FAILED",
        "PAID",
        "FULFILLED",
        "CANCELLED",
        "REFUNDED",
    ),
    "payment_provider": ("razorpay", "manual"),
    "payment_status": (
        "PENDING",
        "PROCESSING",
        "SUCCESS",
        "FAILED",
        "CANCELLED",
        "REFUNDED",
    ),
    "fulfillment_status": ("PENDING", "READY", "DELIVERED", "FAILED"),
    "knowledge_status": ("draft", "published", "archived"),
    "ticket_status": (
        "OPEN",
        "IN_PROGRESS",
        "WAITING_CUSTOMER",
        "RESOLVED",
        "CLOSED",
    ),
    "ticket_priority": ("LOW", "MEDIUM", "HIGH", "URGENT"),
    "webhook_source": ("whatsapp", "telegram", "razorpay"),
    "webhook_status": (
        "received",
        "processing",
        "processed",
        "failed",
        "ignored",
    ),
}


def _enum(name: str) -> postgresql.ENUM:
    """Reference an already-created type without re-issuing CREATE TYPE."""
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


# Generated tsvector expressions. Kept as literals here rather than imported
# from the models: a migration is a frozen snapshot, and importing live model
# code would silently rewrite history the next time the weights change.
PRODUCT_SEARCH_DOC = """
    setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(category, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(sku, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(description, '')), 'C')
"""

KNOWLEDGE_SEARCH_DOC = """
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(array_to_string(keywords, ' '), '')), 'B') ||
    setweight(to_tsvector('english', coalesce(content, '')), 'C')
"""


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _business_fk() -> sa.Column:
    return sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # ---------------------------------------------------------------- businesses
    op.create_table(
        "businesses",
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "timezone",
            sa.String(64),
            server_default="Asia/Kolkata",
            nullable=False,
        ),
        sa.Column(
            "status", _enum("business_status"), server_default="active", nullable=False
        ),
        sa.Column(
            "settings", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_businesses"),
        sa.UniqueConstraint("slug", name="uq_businesses_slug"),
    )

    # ----------------------------------------------------------------- customers
    op.create_table(
        "customers",
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_customers_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_customers_business_id", "customers", ["business_id"])
    op.create_index(
        "uq_customers_business_id_phone",
        "customers",
        ["business_id", "phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )

    # ------------------------------------------------------------------ products
    op.create_table(
        "products",
        sa.Column("sku", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column(
            "status", _enum("product_status"), server_default="active", nullable=False
        ),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        sa.Column(
            "search_doc",
            postgresql.TSVECTOR(),
            sa.Computed(PRODUCT_SEARCH_DOC, persisted=True),
            nullable=True,
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("business_id", "sku", name="uq_products_business_id_sku"),
        sa.CheckConstraint("price >= 0", name="ck_products_price_non_negative"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_products_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_products_business_id", "products", ["business_id"])
    op.create_index(
        "ix_products_business_id_status", "products", ["business_id", "status"]
    )
    op.create_index(
        "ix_products_business_id_category", "products", ["business_id", "category"]
    )

    # ----------------------------------------------------------------- knowledge
    op.create_table(
        "knowledge",
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("knowledge_status"),
            server_default="published",
            nullable=False,
        ),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        sa.Column(
            "search_doc",
            postgresql.TSVECTOR(),
            sa.Computed(KNOWLEDGE_SEARCH_DOC, persisted=True),
            nullable=True,
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_knowledge_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_knowledge_business_id", "knowledge", ["business_id"])

    # --------------------------------------------------------- customer_channels
    op.create_table(
        "customer_channels",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", _enum("channel"), nullable=False),
        sa.Column("external_user_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_customer_channels"),
        sa.UniqueConstraint(
            "business_id",
            "channel",
            "external_user_id",
            name="uq_customer_channels_business_id_channel_external_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_customer_channels_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_customer_channels_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_customer_channels_business_id", "customer_channels", ["business_id"]
    )
    op.create_index(
        "ix_customer_channels_customer_id", "customer_channels", ["customer_id"]
    )

    # ------------------------------------------------------------- conversations
    op.create_table(
        "conversations",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "customer_channel_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("channel", _enum("channel"), nullable=False),
        sa.Column("external_conversation_id", sa.String(128), nullable=True),
        sa.Column(
            "status",
            _enum("conversation_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "current_state", sa.String(48), server_default="NEW", nullable=False
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_conversations_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_channel_id"],
            ["customer_channels.id"],
            name="fk_conversations_customer_channel_id_customer_channels",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_conversations_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_conversations_business_id", "conversations", ["business_id"])
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"])
    op.create_index(
        "ix_conversations_business_id_status_last_message_at",
        "conversations",
        ["business_id", "status", "last_message_at"],
    )
    # One active conversation per channel identity - see ConversationService.
    op.create_index(
        "uq_conversations_active_per_channel",
        "conversations",
        ["customer_channel_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # ------------------------------------------------------------------ messages
    op.create_table(
        "messages",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "seq", sa.BigInteger(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("sender_type", _enum("sender_type"), nullable=False),
        sa.Column(
            "message_type",
            _enum("message_type"),
            server_default="text",
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("message_status"),
            server_default="received",
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        sa.Column("external_message_id", sa.String(128), nullable=True),
        sa.Column("tool_use_id", sa.String(64), nullable=True),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.UniqueConstraint("seq", name="uq_messages_seq"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_messages_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_messages_business_id", "messages", ["business_id"])
    op.create_index("ix_messages_tool_use_id", "messages", ["tool_use_id"])
    op.create_index(
        "ix_messages_conversation_id_seq", "messages", ["conversation_id", "seq"]
    )
    op.create_index(
        "uq_messages_conversation_id_external_message_id",
        "messages",
        ["conversation_id", "external_message_id"],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
    )

    # -------------------------------------------------------------------- orders
    op.create_table(
        "orders",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference", sa.String(32), nullable=False),
        sa.Column(
            "status", _enum("order_status"), server_default="DRAFT", nullable=False
        ),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "discount", sa.Numeric(12, 2), server_default="0", nullable=False
        ),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint(
            "business_id", "reference", name="uq_orders_business_id_reference"
        ),
        sa.CheckConstraint("subtotal >= 0", name="ck_orders_subtotal_non_negative"),
        sa.CheckConstraint("discount >= 0", name="ck_orders_discount_non_negative"),
        sa.CheckConstraint("total >= 0", name="ck_orders_total_non_negative"),
        sa.CheckConstraint(
            "total = subtotal - discount", name="ck_orders_total_matches_components"
        ),
        # RESTRICT, not CASCADE: deleting a customer must not silently erase
        # their financial history.
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_orders_customer_id_customers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_orders_conversation_id_conversations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_orders_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_orders_business_id", "orders", ["business_id"])
    op.create_index("ix_orders_conversation_id", "orders", ["conversation_id"])
    op.create_index("ix_orders_business_id_status", "orders", ["business_id", "status"])
    op.create_index(
        "ix_orders_customer_id_created_at", "orders", ["customer_id", "created_at"]
    )

    # --------------------------------------------------------------- order_items
    op.create_table(
        "order_items",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("product_sku", sa.String(64), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.CheckConstraint(
            "unit_price >= 0", name="ck_order_items_unit_price_non_negative"
        ),
        sa.CheckConstraint(
            "total = unit_price * quantity", name="ck_order_items_total_matches_line"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_items_order_id_orders",
            ondelete="CASCADE",
        ),
        # SET NULL keeps the line readable via its snapshot columns after the
        # product row is gone.
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_order_items_product_id_products",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    # ------------------------------------------------------------------ payments
    op.create_table(
        "payments",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider",
            _enum("payment_provider"),
            server_default="razorpay",
            nullable=False,
        ),
        sa.Column("provider_payment_id", sa.String(128), nullable=True),
        sa.Column("provider_order_id", sa.String(128), nullable=True),
        sa.Column("provider_payment_link_id", sa.String(128), nullable=True),
        sa.Column("payment_url", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column(
            "status", _enum("payment_status"), server_default="PENDING", nullable=False
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "is_duplicate", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "needs_refund", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "raw_payload", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_payments_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_payments_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_payments_business_id", "payments", ["business_id"])
    op.create_index(
        "ix_payments_business_id_status", "payments", ["business_id", "status"]
    )
    op.create_index(
        "ix_payments_order_id_created_at", "payments", ["order_id", "created_at"]
    )
    # The webhook idempotency backbone (rule 9).
    op.create_index(
        "uq_payments_provider_provider_payment_id",
        "payments",
        ["provider", "provider_payment_id"],
        unique=True,
        postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
    )
    # Refund queue for double charges (rule 7).
    op.create_index(
        "ix_payments_needs_refund",
        "payments",
        ["business_id"],
        postgresql_where=sa.text("needs_refund = true"),
    )

    # -------------------------------------------------------------- fulfillments
    op.create_table(
        "fulfillments",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            _enum("fulfillment_status"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        # No credential columns, by design (rule 10). metadata carries only a
        # credential_ref handle into a real secrets manager.
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_fulfillments"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_fulfillments_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_fulfillments_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_fulfillments_business_id", "fulfillments", ["business_id"])
    op.create_index("ix_fulfillments_order_id", "fulfillments", ["order_id"])
    op.create_index(
        "ix_fulfillments_business_id_status", "fulfillments", ["business_id", "status"]
    )

    # ----------------------------------------------------------- support_tickets
    op.create_table(
        "support_tickets",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference", sa.String(32), nullable=False),
        sa.Column(
            "status", _enum("ticket_status"), server_default="OPEN", nullable=False
        ),
        sa.Column(
            "priority",
            _enum("ticket_priority"),
            server_default="MEDIUM",
            nullable=False,
        ),
        sa.Column("assigned_to", sa.String(128), nullable=True),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_support_tickets"),
        sa.UniqueConstraint(
            "business_id", "reference", name="uq_support_tickets_business_id_reference"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_support_tickets_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_support_tickets_conversation_id_conversations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_support_tickets_order_id_orders",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_support_tickets_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_support_tickets_business_id", "support_tickets", ["business_id"]
    )
    op.create_index(
        "ix_support_tickets_assigned_to", "support_tickets", ["assigned_to"]
    )
    op.create_index(
        "ix_support_tickets_business_id_status_priority",
        "support_tickets",
        ["business_id", "status", "priority"],
    )

    # ------------------------------------------------------------ webhook_events
    op.create_table(
        "webhook_events",
        sa.Column("source", _enum("webhook_source"), nullable=False),
        sa.Column("external_event_id", sa.String(191), nullable=False),
        # Nullable: the row is written before the payload is resolved to a
        # tenant, so an unparseable payload is still recorded.
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "signature_verified",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            _enum("webhook_status"),
            server_default="received",
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        _id(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_events"),
        sa.UniqueConstraint(
            "source",
            "external_event_id",
            name="uq_webhook_events_source_external_event_id",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_webhook_events_business_id_businesses",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_webhook_events_status_created_at",
        "webhook_events",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_webhook_events_business_id_source",
        "webhook_events",
        ["business_id", "source"],
    )

    # ---------------------------------------------------------------- agent_runs
    op.create_table(
        "agent_runs",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("effort", sa.String(16), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cache_read_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "cache_creation_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("iterations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tool_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        _id(),
        _business_fk(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_agent_runs_conversation_id_conversations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_agent_runs_business_id_businesses",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_runs_business_id", "agent_runs", ["business_id"])
    op.create_index(
        "ix_agent_runs_business_id_created_at",
        "agent_runs",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_conversation_id_created_at",
        "agent_runs",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    # Reverse dependency order. Indexes go with their tables, so only the
    # tables and then the enum types need explicit drops.
    for table in (
        "agent_runs",
        "webhook_events",
        "support_tickets",
        "fulfillments",
        "payments",
        "order_items",
        "orders",
        "messages",
        "conversations",
        "customer_channels",
        "knowledge",
        "products",
        "customers",
        "businesses",
    ):
        op.drop_table(table)

    bind = op.get_bind()
    for name in ENUMS:
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
