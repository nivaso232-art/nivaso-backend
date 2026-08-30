"""Conversation and message persistence.

Also home to **rule 5**: every tool call the agent makes is written here as two
rows - a ``tool_call`` and a ``tool_result`` sharing a ``tool_use_id`` - before
the loop continues. That is what makes "why did the bot say that?" answerable
weeks later.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.models.enums import (
    Channel,
    ConversationState,
    ConversationStatus,
    MessageStatus,
    MessageType,
    SenderType,
)
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.services.state_machine import assert_conversation_transition

log = structlog.get_logger(__name__)

# How much history to replay into the model. Tool traffic is included because
# the model needs to see what it already looked up - without it, it re-runs
# the same search every turn.
DEFAULT_HISTORY_LIMIT = 40


class ConversationService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.conversations = ConversationRepository(session, business_id)
        self.messages = MessageRepository(session, business_id)

    # -- conversation lifecycle -------------------------------------------

    async def get_or_create_active(
        self,
        *,
        customer_id: uuid.UUID,
        customer_channel_id: uuid.UUID,
        channel: Channel,
        external_conversation_id: str | None = None,
    ) -> Conversation:
        """The active conversation for this channel identity, creating one if
        needed.

        The partial unique index ``uq_conversations_active_per_channel`` is what
        makes this safe under concurrency: batched webhooks mean two handlers
        can both see "no active conversation", and the database picks a winner
        rather than leaving the customer with two parallel threads.
        """
        existing = await self.conversations.get_active_for_channel(customer_channel_id)
        if existing is not None:
            return existing

        conversation = Conversation(
            customer_id=customer_id,
            customer_channel_id=customer_channel_id,
            channel=channel,
            external_conversation_id=external_conversation_id,
            status=ConversationStatus.ACTIVE,
            current_state=ConversationState.NEW.value,
        )

        savepoint = await self.session.begin_nested()
        try:
            await self.conversations.add(conversation)
            await savepoint.commit()
            log.info("conversation_created", conversation_id=str(conversation.id))
            return conversation
        except IntegrityError:
            await savepoint.rollback()
            winner = await self.conversations.get_active_for_channel(
                customer_channel_id
            )
            if winner is None:  # pragma: no cover - a different constraint failed
                raise
            log.info("conversation_race_resolved", conversation_id=str(winner.id))
            return winner

    async def set_state(
        self, conversation: Conversation, target: ConversationState
    ) -> Conversation:
        """Move the conversation's narrative state.

        Never touches order or payment state - that separation is rule 8.
        """
        assert_conversation_transition(conversation.state, target)
        conversation.current_state = target.value
        await self.session.flush()
        return conversation

    async def close(self, conversation: Conversation) -> Conversation:
        conversation.status = ConversationStatus.CLOSED
        conversation.current_state = ConversationState.COMPLETED.value
        await self.session.flush()
        return conversation

    # -- message writes ---------------------------------------------------

    async def record_inbound(
        self,
        *,
        conversation: Conversation,
        content: str | None,
        message_type: MessageType = MessageType.TEXT,
        external_message_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Message | None:
        """Persist a customer message.

        Returns ``None`` if this ``external_message_id`` is already stored -
        the caller should then stop, not run another agent turn. This is the
        message-level half of webhook idempotency (``webhook_events`` covers
        the envelope; this covers a message re-sent inside a new envelope).
        """
        if external_message_id and await self.messages.exists_external_id(
            conversation_id=conversation.id,
            external_message_id=external_message_id,
        ):
            log.info(
                "inbound_message_duplicate",
                external_message_id=external_message_id,
                conversation_id=str(conversation.id),
            )
            return None

        message = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.CUSTOMER,
            message_type=message_type,
            status=MessageStatus.RECEIVED,
            content=content,
            payload=payload or {},
            external_message_id=external_message_id,
        )
        await self.messages.add(message)
        await self._touch(conversation)
        return message

    async def record_assistant_reply(
        self,
        *,
        conversation: Conversation,
        content: str,
        status: MessageStatus = MessageStatus.PENDING,
        payload: dict[str, Any] | None = None,
    ) -> Message:
        """Persist the AI's reply.

        Written as PENDING *before* the send attempt so a message that fails
        to deliver still exists in the log. Recording it only on success would
        mean a failed send leaves no trace of what the AI intended to say.
        """
        message = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.ASSISTANT,
            message_type=MessageType.TEXT,
            status=status,
            content=content,
            payload=payload or {},
        )
        await self.messages.add(message)
        await self._touch(conversation)
        return message

    async def record_tool_call(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        arguments: dict[str, Any],
        tool_use_id: str,
    ) -> Message:
        """Rule 5, first half."""
        message = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.ASSISTANT,
            message_type=MessageType.TOOL_CALL,
            status=MessageStatus.RECEIVED,
            content=None,
            payload={"tool": tool_name, "arguments": arguments},
            tool_use_id=tool_use_id,
        )
        return await self.messages.add(message)

    async def record_tool_result(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        result: Any,
        tool_use_id: str,
        is_error: bool = False,
    ) -> Message:
        """Rule 5, second half."""
        message = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.TOOL,
            message_type=MessageType.TOOL_RESULT,
            status=(
                MessageStatus.FAILED if is_error else MessageStatus.RECEIVED
            ),
            content=None,
            payload={"tool": tool_name, "result": result, "is_error": is_error},
            tool_use_id=tool_use_id,
        )
        return await self.messages.add(message)

    async def mark_delivery(
        self, message: Message, *, status: MessageStatus, error: str | None = None
    ) -> Message:
        message.status = status
        if error:
            message.payload = {**message.payload, "delivery_error": error}
        await self.session.flush()
        return message

    async def record_system_note(
        self, *, conversation: Conversation, note: str
    ) -> Message:
        """An operational event worth showing in the transcript, e.g.
        "payment verified" or "escalated to human agent"."""
        message = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.SYSTEM,
            message_type=MessageType.SYSTEM_NOTE,
            status=MessageStatus.RECEIVED,
            content=note,
        )
        return await self.messages.add(message)

    # -- reads ------------------------------------------------------------

    async def history(
        self,
        conversation: Conversation,
        *,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> Sequence[Message]:
        return await self.messages.list_for_conversation(
            conversation.id, limit=limit, include_tool_traffic=True
        )

    async def _touch(self, conversation: Conversation) -> None:
        conversation.last_message_at = datetime.now(timezone.utc)
        await self.session.flush()
