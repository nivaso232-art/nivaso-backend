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
from app.agent.factory import build_agent_runner
from app.channels.telegram.parser import InboundMessage, parse_update
from app.core.config import settings
from app.core.db import SessionFactory
from app.core.errors import NotFoundError, ProviderError, SignatureError
from app.core.logging import bind_request_context, clear_request_context
from app.core.security import verify_telegram_secret
from app.core.uow import UnitOfWork
from app.models.enums import Channel, MessageType, WebhookSource
from app.providers.telegram.client import TelegramClient
from app.repositories.business_channels import BusinessChannelRepository
from app.repositories.businesses import BusinessRepository
from app.repositories.webhook_events import WebhookEventRepository
from app.services.catalog_service import CatalogService
from app.services.conversation_service import ConversationService
from app.services.customer_service import CustomerService
from app.services.knowledge_service import KnowledgeService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/telegram", tags=["webhooks"])


@router.post("")
async def receive_update_deprecated(
    request: Request,
) -> Response:
    """DEPRECATED. Register your bot at /webhooks/telegram/{slug} instead.

    Each business now has its own webhook URL that includes its slug.
    This catch-all route is kept only to return a clear error so misconfigured
    bots get an actionable message rather than a silent 404.
    """
    log.warning(
        "telegram_webhook_deprecated",
        hint="Register your Telegram bot webhook at /webhooks/telegram/{slug}",
    )
    return Response(
        status_code=410,
        content=(
            b'{"error": "This webhook URL is deprecated. '
            b'Register your bot at /webhooks/telegram/{business-slug} instead."}'
        ),
        media_type="application/json",
    )


@router.post("/{slug}")
async def receive_update_for_business(
    slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Inbound Telegram updates — per-business multi-tenant path.

    Register each business bot with:
        setWebhook url=<host>/webhooks/telegram/<slug>
                    secret_token=<business_telegram_webhook_secret>
    """
    # Quick DB lookup to get this business's webhook secret for verification.
    async with SessionFactory() as quick_session:
        biz_repo = BusinessRepository(quick_session)
        biz = await biz_repo.get_by_slug(slug)
        if biz is None:
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        ch_repo = BusinessChannelRepository(quick_session)
        channel_cfg = await ch_repo.get_for_business(biz.id, "telegram")

    webhook_secret = ""
    tg_credentials: dict | None = None
    if channel_cfg:
        webhook_secret = channel_cfg.credentials.get("webhook_secret", "")
        tg_credentials = channel_cfg.credentials

    try:
        verify_telegram_secret(
            expected=webhook_secret,
            header=x_telegram_bot_api_secret_token,
        )
    except SignatureError as exc:
        log.warning("telegram_signature_invalid", slug=slug, error=str(exc))
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    payload = await request.json()
    background_tasks.add_task(_process_telegram_update, payload, business_slug=slug, tg_credentials=tg_credentials)
    return Response(status_code=status.HTTP_200_OK)


async def _process_telegram_update(
    payload: dict,
    *,
    business_slug: str | None,
    tg_credentials: dict | None,
) -> None:
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
            await _handle_message(session, msg, business_slug=business_slug, tg_credentials=tg_credentials)
            async with UnitOfWork(session):
                await webhook_repo.mark_processed(event)
        except Exception as exc:
            log.exception("telegram_processing_failed", error=str(exc))
            async with UnitOfWork(session):
                await webhook_repo.mark_failed(event, str(exc))


async def _handle_message(
    session: AsyncSession,
    msg: InboundMessage,
    *,
    business_slug: str | None = None,
    tg_credentials: dict | None = None,
) -> None:
    bind_request_context(channel="telegram", external_id=msg.chat_id)

    slug = business_slug
    if not slug:
        log.error(
            "telegram_no_slug",
            hint="Use /webhooks/telegram/{slug} — the slug-less route is deprecated",
        )
        return

    try:
        async with UnitOfWork(session):
            businesses = BusinessRepository(session)
            business = await businesses.get_active_or_raise(slug)

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

            # Inside the UoW so the session has no open transaction when the
            # runner opens its own UnitOfWork; otherwise that UoW is a
            # non-committing passthrough and the reply + tool rows are lost.
            catalog_svc = CatalogService(session, business.id)
            knowledge_svc = KnowledgeService(session, business.id)
            categories = await catalog_svc.list_categories()
            summary = await knowledge_svc.index_summary()
            knowledge_titles = [a["title"] for a in summary]

    except NotFoundError as exc:
        log.error("telegram_business_not_found", slug=slug, error=str(exc))
        return

    try:
        ctx = ToolContext(
            session=session,
            business=business,
            customer=customer,
            conversation=conversation,
        )
        runner = build_agent_runner(ctx)
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
        bot_token = (tg_credentials or {}).get("bot_token") or None
        tg_client = TelegramClient(bot_token=bot_token)
        await tg_client.send_message(chat_id=msg.chat_id, text=reply)
        log.info("telegram_reply_sent", chat_id=msg.chat_id)
    except ProviderError as exc:
        log.error("telegram_send_failed", chat_id=msg.chat_id, error=str(exc))
    finally:
        clear_request_context()
