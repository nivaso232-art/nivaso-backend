"""Telegram Bot API webhook handler.

``POST /webhooks/telegram``
    Inbound updates from Telegram. The flow:
      1. Verify the X-Telegram-Bot-Api-Secret-Token header.
      2. Return 200 immediately (Telegram retries on non-2xx).
      3. Record a ``webhook_event`` row for idempotency.
      4. Parse the update; ignore non-message updates.
      5. Resolve business → customer → conversation.
      6. Record the inbound message; skip if already seen.
      7. Run the agent turn.
      8. Send the reply via the Telegram Bot API.

Register the webhook URL with Telegram once via:
    curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
         -d "url=https://your-host/webhooks/telegram" \
         -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ToolContext
from app.agent.runner import AgentRunner
from app.channels.telegram.parser import InboundMessage, parse_update
from app.core.config import settings
from app.core.db import SessionFactory
from app.core.errors import NotFoundError, ProviderError, SignatureError
from app.core.logging import bind_request_context, clear_request_context
from app.core.security import verify_telegram_secret
from app.core.uow import UnitOfWork
from app.models.enums import Channel, MessageType, WebhookSource
from app.providers.telegram.client import TelegramClient
from app.repositories.businesses import BusinessRepository
from app.repositories.webhook_events import WebhookEventRepository
from app.services.catalog_service import CatalogService
from app.services.conversation_service import ConversationService
from app.services.customer_service import CustomerService
from app.services.knowledge_service import KnowledgeService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/telegram", tags=["webhooks"])


@router.post("")
async def receive_update(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Inbound Telegram updates."""
    try:
        verify_telegram_secret(
            expected=settings.telegram_webhook_secret,
            header=x_telegram_bot_api_secret_token,
        )
    except SignatureError as exc:
        log.warning("telegram_signature_invalid", error=str(exc))
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    payload = await request.json()
    background_tasks.add_task(_process_telegram_update, payload)
    return Response(status_code=status.HTTP_200_OK)


async def _process_telegram_update(payload: dict) -> None:
    """Background processing of a Telegram update."""
    msg = parse_update(payload)
    if msg is None:
        return

    async with SessionFactory() as session:
        webhook_repo = WebhookEventRepository(session)

        async with UnitOfWork(session):
            event = await webhook_repo.record_if_new(
                source=WebhookSource.TELEGRAM,
                external_event_id=msg.external_event_id,
                payload=payload,
                signature_verified=True,
            )
            if event is None:
                log.info("telegram_webhook_duplicate", event_id=msg.external_event_id)
                return
            await webhook_repo.mark_processing(event)

        try:
            await _handle_message(session, msg)
            async with UnitOfWork(session):
                await webhook_repo.mark_processed(event)
        except Exception as exc:
            log.exception("telegram_processing_failed", error=str(exc))
            async with UnitOfWork(session):
                await webhook_repo.mark_failed(event, str(exc))


async def _handle_message(session: AsyncSession, msg: InboundMessage) -> None:
    bind_request_context(channel="telegram", external_id=msg.chat_id)

    if not settings.default_business_slug:
        log.error("telegram_no_business_slug_configured")
        return

    try:
        async with UnitOfWork(session):
            businesses = BusinessRepository(session)
            business = await businesses.get_active_or_raise(settings.default_business_slug)

            customer_svc = CustomerService(session, business.id)
            customer, channel_row = await customer_svc.resolve_or_create(
                channel=Channel.TELEGRAM,
                external_user_id=msg.chat_id,
                display_name=msg.display_name,
            )

            conv_svc = ConversationService(session, business.id)
            conversation = await conv_svc.get_or_create_active(
                customer_id=customer.id,
                customer_channel_id=channel_row.id,
                channel=Channel.TELEGRAM,
            )

            # Load history before recording so the runner gets history + user_text
            # as the current message, not both from history and as a duplicate append.
            history = await conv_svc.history(conversation)

            inbound = await conv_svc.record_inbound(
                conversation=conversation,
                content=msg.text,
                message_type=MessageType.TEXT if msg.text else MessageType.DOCUMENT,
                external_message_id=msg.external_message_id,
                payload={"raw": msg.raw},
            )
            if inbound is None:
                return

    except NotFoundError as exc:
        log.error("telegram_business_not_found", slug=settings.default_business_slug, error=str(exc))
        return

    try:
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
        runner = AgentRunner(ctx)
        reply = await runner.run(
            history=history,
            user_text=msg.text or "[non-text message]",
            categories=categories,
            knowledge_titles=knowledge_titles,
        )
    except Exception as exc:
        log.exception("telegram_agent_failed", error=str(exc))
        reply = "Sorry, something went wrong. Please try again in a moment."

    try:
        tg_client = TelegramClient()
        await tg_client.send_message(chat_id=msg.chat_id, text=reply)
        log.info("telegram_reply_sent", chat_id=msg.chat_id)
    except ProviderError as exc:
        log.error("telegram_send_failed", chat_id=msg.chat_id, error=str(exc))
    finally:
        clear_request_context()
