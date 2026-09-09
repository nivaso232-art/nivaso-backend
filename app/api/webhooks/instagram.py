"""Instagram Messaging webhook handlers.

Two endpoints, mirroring the WhatsApp handler:

``GET /webhooks/instagram``
    Meta's verification handshake. Echoes ``hub.challenge`` when
    ``hub.verify_token`` matches. Must be reachable before the webhook is
    registered under the Instagram product in the Meta app dashboard.

``POST /webhooks/instagram``
    Inbound DMs. Flow:
      1. Verify HMAC-SHA256 signature (X-Hub-Signature-256), if a secret exists.
      2. Return 200 immediately so Meta doesn't retry / duplicate-deliver.
      3. Record a ``webhook_event`` row for idempotency; skip duplicates.
      4. Parse the payload; skip echoes / read receipts.
      5. Resolve business (by receiving IG account id) → customer → conversation.
      6. Record the inbound message; skip if already seen.
      7. Run the agent turn — the *same* pipeline WhatsApp/Telegram use.
      8. Send the reply via the Instagram Send API.
"""

from __future__ import annotations

import json
import time
import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ToolContext
from app.agent.factory import build_agent_runner
from app.channels.instagram.parser import InboundMessage, is_status_only, parse_webhook
from app.core.config import settings
from app.core.db import SessionFactory
from app.core.errors import NotFoundError, ProviderError, SignatureError
from app.core.logging import bind_request_context, clear_request_context
from app.core.security import verify_hmac_sha256
from app.core.uow import UnitOfWork
from app.models.enums import Channel, MessageType, WebhookSource
from app.providers.instagram.client import InstagramClient
from app.repositories.business_channels import BusinessChannelRepository
from app.repositories.businesses import BusinessRepository
from app.repositories.webhook_events import WebhookEventRepository
from app.services.catalog_service import CatalogService
from app.services.conversation_service import ConversationService
from app.services.customer_service import CustomerService
from app.services.knowledge_service import KnowledgeService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/instagram", tags=["webhooks"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    """Meta webhook verification challenge."""
    if hub_mode == "subscribe":
        # Resolve expected token: env var → DB (any instagram channel) → None.
        expected: str | None = settings.instagram_verify_token or None

        if expected is None:
            async with SessionFactory() as s:
                channels = await BusinessChannelRepository(s).list_by_channel_type("instagram")
                for ch in channels:
                    stored = ch.credentials.get("verify_token", "")
                    if stored:
                        expected = stored
                        break

        if expected is None:
            # Nothing configured — accept any challenge. Real security is the
            # HMAC signature on POST requests.
            log.warning("instagram_verify_token_not_configured_accepting_challenge")
            return Response(content=hub_challenge, media_type="text/plain")

        if hub_verify_token == expected:
            return Response(content=hub_challenge, media_type="text/plain")

    log.warning("instagram_verification_failed", mode=hub_mode)
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
) -> Response:
    """Inbound Instagram DMs."""
    raw_body = await request.body()

    # Resolve app_secret: global env var first; fall back to the per-business
    # secret stored in the instagram channel credentials.
    app_secret = settings.instagram_app_secret
    if not app_secret:
        try:
            async with SessionFactory() as _s:
                channels = await BusinessChannelRepository(_s).list_by_channel_type("instagram")
                for ch in channels:
                    secret = ch.credentials.get("app_secret", "")
                    if secret:
                        app_secret = secret
                        break
        except Exception:
            pass

    if app_secret:
        try:
            verify_hmac_sha256(
                secret=app_secret,
                payload=raw_body,
                provided=x_hub_signature_256,
                prefix="sha256=",
                source="instagram",
            )
        except SignatureError as exc:
            log.warning("instagram_signature_invalid", error=str(exc))
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    else:
        log.warning(
            "instagram_app_secret_not_configured",
            hint="Set INSTAGRAM_APP_SECRET or save app_secret in the channel config",
        )

    payload = json.loads(raw_body)

    # Return 200 immediately; process in background so a slow agent turn never
    # trips Meta's retry → duplicate delivery.
    background_tasks.add_task(_process_instagram_payload, payload)
    return Response(status_code=status.HTTP_200_OK)


async def _process_instagram_payload(payload: dict) -> None:
    """Background processing of an Instagram webhook payload."""
    if is_status_only(payload):
        return

    messages = parse_webhook(payload)
    if not messages:
        return

    async with SessionFactory() as session:
        webhook_repo = WebhookEventRepository(session)
        external_event_id = messages[0].external_event_id

        async with UnitOfWork(session):
            event = await webhook_repo.record_if_new(
                source=WebhookSource.INSTAGRAM,
                external_event_id=external_event_id,
                payload=payload,
                signature_verified=True,
            )
            if event is None:
                log.info("instagram_webhook_duplicate", event_id=external_event_id)
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
            log.exception("instagram_processing_failed", error=str(exc))
            async with UnitOfWork(session):
                await webhook_repo.mark_failed(event, str(exc))


async def _resolve_channel(ch_repo: BusinessChannelRepository, recipient_id: str):
    """Find the instagram channel that received this DM.

    Primary: match ``recipient_id`` against ``external_channel_id``. Meta may
    send either the IG-scoped account id or the app-scoped id here depending on
    the API version, so if that misses we fall back to the sole active instagram
    channel (single-tenant-safe) and log the id we actually saw.
    """
    channel_cfg = await ch_repo.get_by_external_id("instagram", recipient_id)
    if channel_cfg:
        return channel_cfg

    channels = list(await ch_repo.list_by_channel_type("instagram"))
    if len(channels) == 1:
        log.warning(
            "instagram_routing_fallback_single_channel",
            recipient_id=recipient_id,
            external_channel_id=channels[0].external_channel_id,
            hint="Update external_channel_id to this recipient_id for exact routing",
        )
        return channels[0]

    # Multiple instagram channels and no exact id match — also try matching the
    # stored app-scoped id in credentials before giving up.
    for ch in channels:
        if str(ch.credentials.get("app_scoped_id", "")) == recipient_id:
            return ch
    return None


async def _handle_message(session: AsyncSession, msg: InboundMessage) -> uuid.UUID | None:
    """Resolve context, run the agent turn, send the reply.

    Routing: business_channels table — match by the receiving IG account id.
    Returns the resolved business_id so the caller can stamp the webhook_event.
    """
    started_at = time.monotonic()
    bind_request_context(channel="instagram", external_id=msg.sender_id)

    ig_credentials: dict = {}

    try:
        async with UnitOfWork(session):
            businesses = BusinessRepository(session)
            ch_repo = BusinessChannelRepository(session)

            channel_cfg = await _resolve_channel(ch_repo, msg.recipient_id)
            if not channel_cfg:
                log.error(
                    "instagram_no_business_for_account",
                    recipient_id=msg.recipient_id,
                    hint="Configure this IG account: Admin → Business → Channels → Instagram",
                )
                return None

            business = await businesses.get_or_raise(channel_cfg.business_id)
            ig_credentials = channel_cfg.credentials

            customer_svc = CustomerService(session, business.id)
            customer, channel_row = await customer_svc.resolve_or_create(
                channel=Channel.INSTAGRAM,
                external_user_id=msg.sender_id,
                display_name=None,
            )

            conv_svc = ConversationService(session, business.id)
            conversation = await conv_svc.get_or_create_active(
                customer_id=customer.id,
                customer_channel_id=channel_row.id,
                channel=Channel.INSTAGRAM,
            )

            # Load history BEFORE recording the inbound message so the runner
            # gets history + the new message once, not the message duplicated.
            history = await conv_svc.history(conversation)

            inbound = await conv_svc.record_inbound(
                conversation=conversation,
                content=msg.text,
                message_type=_map_type(msg.message_type),
                external_message_id=msg.external_message_id,
                payload={"raw": msg.raw},
            )
            if inbound is None:
                log.info("instagram_message_duplicate", mid=msg.external_message_id)
                return None

            catalog_svc = CatalogService(session, business.id)
            knowledge_svc = KnowledgeService(session, business.id)
            categories = await catalog_svc.list_categories()
            summary = await knowledge_svc.index_summary()
            knowledge_titles = [a["title"] for a in summary]

    except NotFoundError as exc:
        log.error(
            "instagram_business_inactive",
            recipient_id=msg.recipient_id,
            error=str(exc),
        )
        return None

    # Load entitlements so plan restrictions (tools, model) are enforced.
    try:
        from app.repositories.entitlements import EntitlementRepository
        async with SessionFactory() as ent_session:
            ents = await EntitlementRepository(ent_session).resolved(business.id)
    except Exception:
        ents = None

    # Run the agent turn — the runner opens its own UnitOfWork to commit writes.
    try:
        ctx = ToolContext(
            session=session,
            business=business,
            customer=customer,
            conversation=conversation,
        )
        runner = build_agent_runner(ctx, entitlements=ents)
        reply = await runner.run(
            history=history,
            user_text=msg.text or "[non-text message]",
            categories=categories,
            knowledge_titles=knowledge_titles,
        )
    except Exception as exc:
        log.exception("instagram_agent_failed", error=str(exc))
        reply = "Sorry, something went wrong. Please try again in a moment."
        try:
            async with UnitOfWork(session):
                await conv_svc.record_assistant_reply(
                    conversation=conversation,
                    content=reply,
                )
        except Exception:
            pass

    # Send the reply back using this business's IG access token.
    try:
        ig_client = InstagramClient(
            access_token=ig_credentials.get("access_token") or None,
        )
        mid = await ig_client.send_text(to=msg.sender_id, text=reply)
        channel_latency_ms = int((time.monotonic() - started_at) * 1000)
        log.info(
            "instagram_reply_sent",
            to=msg.sender_id,
            message_id=mid,
            channel_latency_ms=channel_latency_ms,
        )
    except ProviderError as exc:
        channel_latency_ms = int((time.monotonic() - started_at) * 1000)
        log.error(
            "instagram_send_failed",
            to=msg.sender_id,
            error=str(exc),
            channel_latency_ms=channel_latency_ms,
        )
    finally:
        clear_request_context()

    return business.id


def _map_type(raw: str) -> MessageType:
    return {
        "text": MessageType.TEXT,
        "image": MessageType.IMAGE,
        "audio": MessageType.AUDIO,
        "video": MessageType.VIDEO,
        "share": MessageType.TEXT,
        "story_mention": MessageType.TEXT,
    }.get(raw, MessageType.TEXT)
