"""Outbound delivery to a customer over whatever channel they use.

Used by the payment webhook to push credentials to the buyer. Best-effort: a
send failure is logged, not raised, so it never rolls back the delivery record
(the credentials are already allocated and recorded — a human can re-send).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Channel
from app.providers.telegram.client import TelegramClient
from app.providers.whatsapp.client import WhatsAppClient
from app.repositories.customers import CustomerChannelRepository

log = structlog.get_logger(__name__)

# Prefer WhatsApp, then Telegram. WEB has no outbound channel.
_PRIORITY = (Channel.WHATSAPP, Channel.TELEGRAM)


async def send_to_customer(
    session: AsyncSession,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
    text: str,
) -> bool:
    """Send ``text`` to the customer on their best available channel."""
    channels = await CustomerChannelRepository(session, business_id).list_for_customer(
        customer_id
    )
    by_channel = {c.channel: c for c in channels}

    for channel in _PRIORITY:
        row = by_channel.get(channel)
        if row is None:
            continue
        try:
            if channel is Channel.WHATSAPP:
                await WhatsAppClient().send_text(to=row.external_user_id, text=text)
            else:
                await TelegramClient().send_message(chat_id=row.external_user_id, text=text)
            log.info("notify_sent", channel=channel.value, customer_id=str(customer_id))
            return True
        except Exception as exc:
            log.error("notify_failed", channel=channel.value, error=str(exc))

    log.warning("notify_no_deliverable_channel", customer_id=str(customer_id))
    return False
