"""Conversation and message persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, Message
from app.models.enums import ConversationStatus, MessageType
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def get_active_for_channel(
        self, customer_channel_id: uuid.UUID
    ) -> Conversation | None:
        stmt = self._scoped().where(
            Conversation.customer_channel_id == customer_channel_id,
            Conversation.status == ConversationStatus.ACTIVE,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_customer(
        self, customer_id: uuid.UUID, *, limit: int = 20
    ) -> Sequence[Conversation]:
        stmt = (
            self._scoped()
            .where(Conversation.customer_id == customer_id)
            .order_by(Conversation.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_with_messages(
        self, conversation_id: uuid.UUID
    ) -> Conversation | None:
        stmt = (
            self._scoped()
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int = 100,
        include_tool_traffic: bool = True,
    ) -> Sequence[Message]:
        """Most recent messages, returned oldest-first.

        The subquery is what makes that work: ``ORDER BY seq DESC LIMIT n``
        takes the newest window, then the outer query flips it back into
        chronological order. Ordering ascending and limiting would return the
        *oldest* n messages instead - the wrong end of the conversation.
        """
        inner = (
            select(Message.id)
            .where(
                Message.business_id == self.business_id,
                Message.conversation_id == conversation_id,
            )
            .order_by(Message.seq.desc())
            .limit(limit)
        )
        if not include_tool_traffic:
            inner = inner.where(
                Message.message_type.notin_(
                    [MessageType.TOOL_CALL, MessageType.TOOL_RESULT]
                )
            )

        stmt = (
            select(Message)
            .where(Message.id.in_(inner.scalar_subquery()))
            .order_by(Message.seq)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def exists_external_id(
        self, *, conversation_id: uuid.UUID, external_message_id: str
    ) -> bool:
        """Second line of defence against webhook redelivery.

        ``webhook_events`` catches whole-payload replays; this catches the case
        where a provider re-sends the same message inside a *different* event
        envelope, which the unique index on events would not see.
        """
        stmt = select(Message.id).where(
            Message.business_id == self.business_id,
            Message.conversation_id == conversation_id,
            Message.external_message_id == external_message_id,
        )
        return (await self.session.execute(stmt)).first() is not None

    async def get_by_tool_use_id(
        self, tool_use_id: str
    ) -> Sequence[Message]:
        """Both halves of one tool call - the call row and its result row."""
        stmt = (
            self._scoped()
            .where(Message.tool_use_id == tool_use_id)
            .order_by(Message.seq)
        )
        return (await self.session.execute(stmt)).scalars().all()
