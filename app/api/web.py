"""Web test channel.

A synchronous chat endpoint for driving the agent without WhatsApp/Telegram or
any Meta/Razorpay setup. Unlike the messaging webhooks — which return 200
immediately and push the reply back out through a provider — this endpoint runs
the agent turn inline and returns the reply in the HTTP response. That makes it
the fastest way to test a tenant's catalog, prompts, and tools end to end.

It reuses the *same* pipeline as ``webhooks/whatsapp.py``:

    resolve business -> resolve/create customer (Channel.WEB)
    -> get/create conversation -> record inbound -> run agent -> return reply

Protected by ``X-Internal-Key`` (registered with the admin routers in
``main.py``) because a turn spends Anthropic tokens and touches tenant data.
Each ``user_id`` gets its own persistent conversation, so repeated calls with
the same ``user_id`` continue the same thread — exactly like a returning
WhatsApp customer.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ToolContext
from app.agent.factory import build_agent_runner
from app.api.deps import get_session
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.uow import UnitOfWork
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.customer import CustomerChannel
from app.models.enums import Channel, MessageType, SenderType
from app.repositories.businesses import BusinessRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.customers import CustomerChannelRepository
from app.services.catalog_service import CatalogService
from app.services.conversation_service import ConversationService
from app.services.customer_service import CustomerService
from app.services.knowledge_service import KnowledgeService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/web", tags=["web"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="The customer's message.")
    user_id: str = Field(
        default="web-tester",
        min_length=1,
        description="Stable id for this tester. Same id = same conversation.",
    )
    business_slug: str | None = Field(
        default=None,
        description="Which tenant to talk to. Falls back to DEFAULT_BUSINESS_SLUG.",
    )
    display_name: str | None = Field(default=None)
    provider: str | None = Field(
        default=None,
        description="Override LLM provider: 'anthropic' or 'gemini'.",
    )
    model: str | None = Field(
        default=None,
        description="Override the model ID (e.g. 'claude-sonnet-4-6', 'gemini-2.5-flash').",
    )
    admin_mode: bool = Field(
        default=False,
        description=(
            "Unlock admin-only tools (knowledge base create/update/list). "
            "Only accepted from requests that already have the X-Internal-Key header."
        ),
    )


class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any]


class ChatResponse(BaseModel):
    reply: str
    business_slug: str
    conversation_id: str
    customer_id: str
    conversation_state: str
    tools_used: list[ToolCall]
    model_used: str


class HistoryMessage(BaseModel):
    role: str
    content: str


class SessionOut(BaseModel):
    user_id: str
    customer_name: str | None
    conversation_id: str | None
    last_message_at: str | None


def _trailing_tool_calls(history: Any, after_message_id: Any) -> list[ToolCall]:
    """Pull the tool calls the agent made in the turn we just ran.

    Walk the (oldest-first) history to the inbound message we recorded, then
    collect every ``tool_call`` row after it. Purely for test visibility — it
    lets the caller see *how* the reply was produced.
    """
    seen_inbound = False
    calls: list[ToolCall] = []
    for msg in history:
        if msg.id == after_message_id:
            seen_inbound = True
            continue
        if seen_inbound and msg.message_type == MessageType.TOOL_CALL:
            payload = msg.payload or {}
            calls.append(
                ToolCall(
                    tool=payload.get("tool", ""),
                    arguments=payload.get("arguments", {}),
                )
            )
    return calls


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    """Send one message to the agent and get its reply synchronously."""
    slug = body.business_slug or settings.default_business_slug
    if not slug:
        raise NotFoundError(
            "No business specified and DEFAULT_BUSINESS_SLUG is unset.",
            details={"hint": "Pass business_slug in the request body."},
        )

    async with UnitOfWork(session):
        businesses = BusinessRepository(session)
        business = await businesses.get_active_or_raise(slug)

        customer_svc = CustomerService(session, business.id)
        customer, channel_row = await customer_svc.resolve_or_create(
            channel=Channel.WEB,
            external_user_id=body.user_id,
            display_name=body.display_name,
        )

        conv_svc = ConversationService(session, business.id)
        conversation = await conv_svc.get_or_create_active(
            customer_id=customer.id,
            customer_channel_id=channel_row.id,
            channel=Channel.WEB,
        )

        # Load history BEFORE recording the inbound (same ordering fix as the
        # WhatsApp handler) so the runner gets history + the new message once.
        history = await conv_svc.history(conversation)
        inbound = await conv_svc.record_inbound(
            conversation=conversation,
            content=body.message,
            message_type=MessageType.TEXT,
        )

        # Fetch agent orientation inside this UoW too, so the session has no open
        # transaction when the runner opens its own UnitOfWork. Otherwise that UoW
        # sees an existing transaction, becomes a non-committing passthrough, and
        # the assistant reply + tool rows are silently rolled back on close.
        catalog_svc = CatalogService(session, business.id)
        knowledge_svc = KnowledgeService(session, business.id)
        categories = await catalog_svc.list_categories()
        summary = await knowledge_svc.index_summary()
        knowledge_titles = [a["title"] for a in summary]

    ctx = ToolContext(
        session=session,
        business=business,
        customer=customer,
        conversation=conversation,
    )
    runner = build_agent_runner(
        ctx,
        provider=body.provider,
        model=body.model,
        admin_mode=body.admin_mode,
    )
    reply = await runner.run(
        history=history,
        user_text=body.message,
        categories=categories,
        knowledge_titles=knowledge_titles,
    )
    model_used = runner.model

    # Re-read to surface which tools ran this turn (test visibility only).
    after_history = await conv_svc.history(conversation)
    tools_used = (
        _trailing_tool_calls(after_history, inbound.id) if inbound is not None else []
    )

    log.info(
        "web_chat_reply",
        business=slug,
        conversation_id=str(conversation.id),
        tools=[t.tool for t in tools_used],
    )

    return ChatResponse(
        reply=reply,
        business_slug=slug,
        conversation_id=str(conversation.id),
        customer_id=str(customer.id),
        conversation_state=conversation.current_state,
        tools_used=tools_used,
        model_used=model_used,
    )


@router.get("/history", response_model=list[HistoryMessage])
async def get_history(
    user_id: str = "web-tester",
    business_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[HistoryMessage]:
    """Return the TEXT messages for this user's active conversation."""
    slug = business_slug or settings.default_business_slug
    if not slug:
        return []

    try:
        business = await BusinessRepository(session).get_active_or_raise(slug)
    except NotFoundError:
        return []

    channel_repo = CustomerChannelRepository(session, business.id)
    channel_row = await channel_repo.get_by_external_id(
        channel=Channel.WEB, external_user_id=user_id
    )
    if channel_row is None:
        return []

    conversation = await ConversationRepository(session, business.id).get_active_for_channel(
        channel_row.id
    )
    if conversation is None:
        return []

    messages = await MessageRepository(session, business.id).list_for_conversation(
        conversation.id, limit=100, include_tool_traffic=False
    )

    return [
        HistoryMessage(
            role="user" if msg.sender_type == SenderType.CUSTOMER else "assistant",
            content=msg.content or "",
        )
        for msg in messages
        if msg.message_type == MessageType.TEXT and msg.content
    ]


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    business_slug: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[SessionOut]:
    """List all WEB chat sessions for a business, newest activity first."""
    slug = business_slug or settings.default_business_slug
    if not slug:
        return []

    try:
        business = await BusinessRepository(session).get_active_or_raise(slug)
    except NotFoundError:
        return []

    stmt = (
        select(CustomerChannel)
        .where(
            CustomerChannel.business_id == business.id,
            CustomerChannel.channel == Channel.WEB,
        )
        .options(selectinload(CustomerChannel.customer))
    )
    channels = (await session.execute(stmt)).scalars().all()

    conv_repo = ConversationRepository(session, business.id)
    results: list[SessionOut] = []
    for ch in channels:
        conv = await conv_repo.get_active_for_channel(ch.id)
        results.append(
            SessionOut(
                user_id=ch.external_user_id,
                customer_name=(
                    ch.customer.name or ch.display_name if ch.customer else ch.display_name
                ),
                conversation_id=str(conv.id) if conv else None,
                last_message_at=(
                    conv.last_message_at.isoformat()
                    if conv and conv.last_message_at
                    else None
                ),
            )
        )

    results.sort(key=lambda s: s.last_message_at or "", reverse=True)
    return results
