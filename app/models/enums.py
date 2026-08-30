"""Every enum in the system, in one place.

These map to **native Postgres enum types** (see ``app/models/base.py`` ->
``pg_enum``). DB-level integrity matters most on order and payment status,
where a typo'd string would silently create an unreachable state.

Cost of that choice: adding a value needs a migration.

    # in a migration, outside a transaction block
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'ON_HOLD'")

The one exception is ``ConversationState``, which is stored as ``text`` and
validated in Python by ``app/services/state_machine.py`` - it churns most
during development and is not a money-state.
"""

from __future__ import annotations

from enum import StrEnum


class BusinessStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    OUT_OF_STOCK = "out_of_stock"
    ARCHIVED = "archived"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    WEB = "web"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class ConversationState(StrEnum):
    """Where the conversation is, narratively.

    Deliberately decoupled from ``OrderStatus``: a customer can ask about a
    different product mid-checkout (state moves to PRODUCT_ENQUIRY) without
    disturbing their PAYMENT_PENDING order.

    Stored as ``text``, not a PG enum - see module docstring.
    """

    NEW = "NEW"
    PRODUCT_ENQUIRY = "PRODUCT_ENQUIRY"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_VERIFICATION = "PAYMENT_VERIFICATION"
    FULFILLMENT = "FULFILLMENT"
    SUPPORT = "SUPPORT"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    COMPLETED = "COMPLETED"


class SenderType(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"
    AGENT = "agent"  # a human agent, as opposed to the AI


class MessageType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    LOCATION = "location"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM_NOTE = "system_note"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAID = "PAID"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class PaymentProvider(StrEnum):
    RAZORPAY = "razorpay"
    MANUAL = "manual"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class FulfillmentStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class WebhookSource(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    RAZORPAY = "razorpay"


class WebhookStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


# Terminal order states - no further transitions, and the guard rail behind
# "AI cannot cancel a paid order".
TERMINAL_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FULFILLED, OrderStatus.CANCELLED, OrderStatus.REFUNDED}
)

# Payment states that mean money actually moved.
SETTLED_PAYMENT_STATUSES: frozenset[PaymentStatus] = frozenset(
    {PaymentStatus.SUCCESS, PaymentStatus.REFUNDED}
)
