"""WhatsApp Cloud API webhook handlers.

Two endpoints:

``GET /webhooks/whatsapp``
    Meta's verification handshake. Echoes the ``hub.challenge`` query parameter
    back if the ``hub.verify_token`` matches our config. Must be reachable
    *before* the webhook is registered in the Meta app dashboard.

``POST /webhooks/whatsapp``
    Inbound messages and status updates. The flow:
      1. Verify HMAC-SHA256 signature (X-Hub-Signature-256).
      2. Return 200 immediately to avoid Meta's timeout → duplicate delivery.
      3. Record a ``webhook_event`` row for idempotency; skip if duplicate.
      4. Parse the payload; ignore status-only callbacks.
      5. Resolve business → customer → conversation.
      6. Record the inbound message; skip if already seen.
      7. Run the agent turn.
      8. Send the reply back via the WhatsApp API.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ToolContext
from app.agent.factory import build_agent_runner
from app.channels.whatsapp.parser import InboundMessage, is_status_only, parse_webhook
from app.core.config import settings
from app.core.db import SessionFactory
from app.core.errors import NotFoundError, ProviderError, SignatureError
from app.core.logging import bind_request_context, clear_request_context
from app.core.security import verify_meta_signature
from app.core.uow import UnitOfWork
from app.models.enums import Channel, MessageType
from app.providers.whatsapp.client import WhatsAppClient
from app.repositories.business_channels import BusinessChannelRepository
from app.repositories.businesses import BusinessRepository
from app.repositories.webhook_events import WebhookEventRepository
from app.models.enums import WebhookSource
from app.services.catalog_service import CatalogService
from app.services.conversation_service import ConversationService
from app.services.customer_service import CustomerService
from app.services.knowledge_service import KnowledgeService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    """Meta webhook verification challenge."""
    if (
        hub_mode == "subscribe"
        and hub_verify_token == settings.whatsapp_verify_token
    ):
        return Response(content=hub_challenge, media_type="text/plain")

    log.warning(
        "whatsapp_verification_failed",
        mode=hub_mode,
        token_match=hub_verify_token == settings.whatsapp_verify_token,
    )
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
) -> Response:
    """Inbound WhatsApp messages."""
    raw_body = await request.body()

    try:
        verify_meta_signature(
            app_secret=settings.whatsapp_app_secret,
            payload=raw_body,
            header=x_hub_signature_256,
        )
    except SignatureError as exc:
        log.warning("whatsapp_signature_invalid", error=str(exc))
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    payload = await request.json()

    # Return 200 immediately — Meta retries on any non-2xx, and a slow agent
    # must not cause duplicate deliveries. Processing happens in background.
    background_tasks.add_task(_process_whatsapp_payload, payload, raw_body)
    return Response(status_code=status.HTTP_200_OK)


async def _process_whatsapp_payload(
    payload: dict, raw_body: bytes
) -> None:
    """Background processing of a WhatsApp webhook payload."""
    if is_status_only(payload):
        return

    messages = parse_webhook(payload)
    if not messages:
        return

    async with SessionFactory() as session:
        webhook_repo = WebhookEventRepository(session)
        # Use the first message id as the event id (Meta has no per-envelope id).
        external_event_id = messages[0].external_event_id if messages else "unknown"

        async with UnitOfWork(session):
            event = await webhook_repo.record_if_new(
                source=WebhookSource.WHATSAPP,
                external_event_id=external_event_id,
                payload=payload,
                signature_verified=True,
            )
            if event is None:
                log.info("whatsapp_webhook_duplicate", event_id=external_event_id)
                return
            await webhook_repo.mark_processing(event)

        resolved_business_id: uuid.UUID | None = None
        try:
            for msg in messages:
                bid = await _handle_message(session, msg)
                if bid is not None and resolved_business_id is None:
                    resolved_business_id = bid

            async with UnitOfWork(session):
                await webhook_repo.mark_processed(event, business_id=resolved_business_id)
        except Exception as exc:
            log.exception("whatsapp_processing_failed", error=str(exc))
            async with UnitOfWork(session):
                await webhook_repo.mark_failed(event, str(exc))


async def _handle_message(session: AsyncSession, msg: InboundMessage) -> uuid.UUID | None:
    """Resolve context, run the agent turn, send the reply.

    Routing: business_channels table — match by phone_number_id (multi-tenant, DB-driven).
    No fallback. Configure the phone number in the admin panel:
      Business → Channels → WhatsApp

    Returns the resolved business_id so the caller can stamp the webhook_event row.
    """
    bind_request_context(channel="whatsapp", external_id=msg.wa_id)

    wa_credentials: dict = {}

    try:
        async with UnitOfWork(session):
            businesses = BusinessRepository(session)
            ch_repo = BusinessChannelRepository(session)

            channel_cfg = await ch_repo.get_by_external_id("whatsapp", msg.phone_number_id)
            if not channel_cfg:
                log.error(
                    "whatsapp_no_business_for_phone_number",
                    phone_number_id=msg.phone_number_id,
                    hint="Configure this number: Admin → Business → Channels → WhatsApp",
                )
                return None

            business = await businesses.get_or_raise(channel_cfg.business_id)
            wa_credentials = channel_cfg.credentials

            customer_svc = CustomerService(session, business.id)
            customer, channel_row = await customer_svc.resolve_or_create(
                channel=Channel.WHATSAPP,
                external_user_id=msg.wa_id,
                display_name=msg.display_name,
            )

            conv_svc = ConversationService(session, business.id)
            conversation = await conv_svc.get_or_create_active(
                customer_id=customer.id,
                customer_channel_id=channel_row.id,
                channel=Channel.WHATSAPP,
            )

            # Bug fix: load history BEFORE recording the inbound message so
            # the runner receives history + user_text as the current message,
            # not history (which already includes it) + user_text duplicated.
            history = await conv_svc.history(conversation)

            inbound = await conv_svc.record_inbound(
                conversation=conversation,
                content=msg.text,
                message_type=_map_type(msg.message_type),
                external_message_id=msg.external_message_id,
                payload={"raw": msg.raw},
            )
            if inbound is None:
                log.info("whatsapp_message_duplicate", wamid=msg.external_message_id)
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
        log.error(
            "whatsapp_business_inactive",
            phone_number_id=msg.phone_number_id,
            error=str(exc),
        )
        return None

    # Run agent turn — the runner opens its own UnitOfWork to commit its writes.
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
        log.exception("whatsapp_agent_failed", error=str(exc))
        reply = "Sorry, something went wrong. Please try again in a moment."

    # Send reply back using per-business credentials if available.
    try:
        wa_client = WhatsAppClient(
            phone_number_id=wa_credentials.get("phone_number_id") or None,
            access_token=wa_credentials.get("access_token") or None,
        )
        wamid = await wa_client.send_text(to=msg.wa_id, text=reply)
        log.info("whatsapp_reply_sent", to=msg.wa_id, wamid=wamid)
    except ProviderError as exc:
        log.error("whatsapp_send_failed", to=msg.wa_id, error=str(exc))
    finally:
        clear_request_context()

    return business.id


def _map_type(raw: str) -> MessageType:
    return {
        "text": MessageType.TEXT,
        "image": MessageType.IMAGE,
        "audio": MessageType.AUDIO,
        "video": MessageType.VIDEO,
        "document": MessageType.DOCUMENT,
        "location": MessageType.LOCATION,
    }.get(raw, MessageType.TEXT)
