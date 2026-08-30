"""Conversations and the message log.

``messages`` is the append-only audit trail of everything that happened in a
conversation - including the AI's tool calls and their results. That is what
makes "why did the bot say ₹149?" answerable after the fact.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import (
    Channel,
    ConversationState,
    ConversationStatus,
    MessageStatus,
    MessageType,
    SenderType,
)

if TYPE_CHECKING:
    from app.models.customer import Customer, CustomerChannel


class Conversation(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        # One live conversation per channel identity. Enforced in the database
        # because two webhooks arriving concurrently would otherwise both find
        # "no active conversation" and each create one.
        Index(
            "uq_conversations_active_per_channel",
            "customer_channel_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_conversations_business_id_status_last_message_at",
            "business_id",
            "status",
            "last_message_at",
        ),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_channel_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customer_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, "channel"), nullable=False)

    external_conversation_id: Mapped[str | None] = mapped_column(String(128))

    status: Mapped[ConversationStatus] = mapped_column(
        pg_enum(ConversationStatus, "conversation_status"),
        nullable=False,
        server_default=ConversationStatus.ACTIVE.value,
    )

    # Plain text, not a PG enum: this churns most during development and is
    # not a money-state. Validated by app/services/state_machine.py.
    current_state: Mapped[str] = mapped_column(
        String(48), nullable=False, server_default=ConversationState.NEW.value
    )

    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    customer: Mapped[Customer] = relationship(back_populates="conversations")
    customer_channel: Mapped[CustomerChannel] = relationship(
        back_populates="conversations"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.seq",
    )

    @property
    def state(self) -> ConversationState:
        return ConversationState(self.current_state)


class Message(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id_seq", "conversation_id", "seq"),
        # Inbound webhook idempotency at the message level: Meta redelivers on
        # any non-2xx, so the same wamid can arrive several times.
        Index(
            "uq_messages_conversation_id_external_message_id",
            "conversation_id",
            "external_message_id",
            unique=True,
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        Index("ix_messages_tool_use_id", "tool_use_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Monotonic per-table ordering. created_at alone is not enough: several
    # messages in one agent turn (tool_call, tool_result, assistant reply) can
    # share a timestamp, and replaying them out of order corrupts the
    # conversation history sent back to the model.
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), nullable=False, unique=True
    )

    sender_type: Mapped[SenderType] = mapped_column(
        pg_enum(SenderType, "sender_type"), nullable=False
    )
    message_type: Mapped[MessageType] = mapped_column(
        pg_enum(MessageType, "message_type"),
        nullable=False,
        server_default=MessageType.TEXT.value,
    )
    status: Mapped[MessageStatus] = mapped_column(
        pg_enum(MessageStatus, "message_status"),
        nullable=False,
        server_default=MessageStatus.RECEIVED.value,
    )

    # Human-readable text. NULL for tool_call / tool_result rows, where the
    # substance lives in `payload`.
    content: Mapped[str | None] = mapped_column(Text)

    # tool_call  -> {"tool": "search_products", "arguments": {...}}
    # tool_result-> {"tool": "search_products", "result": {...}, "is_error": false}
    # media      -> {"storage_path": "...", "mime_type": "...", "provider_id": "..."}
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    external_message_id: Mapped[str | None] = mapped_column(String(128))

    # The Anthropic tool_use block id. Links a tool_call row to its
    # tool_result row so a turn can be reconstructed exactly.
    tool_use_id: Mapped[str | None] = mapped_column(String(64))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    @property
    def is_tool_traffic(self) -> bool:
        return self.message_type in (MessageType.TOOL_CALL, MessageType.TOOL_RESULT)
