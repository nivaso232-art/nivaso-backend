"""Instagram Messaging webhook payload parser.

Instagram DMs arrive on the Messenger-style envelope (``object: "instagram"``),
*not* the WhatsApp ``changes[].value.messages[]`` shape. One POST can batch
several messaging events; we unpack them into individual ``InboundMessage``
objects, one per real inbound text message.

Events we deliberately skip:
  * ``message.is_echo`` — the business's own outbound message echoed back.
    Processing these would make the bot reply to itself in a loop.
  * ``read`` / ``delivery`` / ``reaction`` / ``postback`` events — no
    ``message.text`` to act on.

Reference:
    https://developers.facebook.com/docs/instagram-platform/webhooks
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InboundMessage:
    """Everything extracted from one Instagram DM."""

    external_message_id: str    # mid — used for message-level idempotency
    external_event_id: str      # mid — used for webhook_events idempotency
    sender_id: str              # sender IGSID — the customer's channel handle
    recipient_id: str           # receiving IG account id — multi-tenant routing key
    text: str | None            # None for non-text message types
    message_type: str           # text | image | audio | video | share | …
    raw: dict[str, Any]         # verbatim messaging object for payload storage


def parse_webhook(payload: dict[str, Any]) -> list[InboundMessage]:
    """Extract inbound DMs from an Instagram messaging webhook payload.

    Returns an empty list for non-message events (echoes, read receipts,
    reactions) so the handler can ignore them.
    """
    messages: list[InboundMessage] = []

    for entry in payload.get("entry", []):
        # entry.id is the business IG account id; recipient.id mirrors it, but
        # we read recipient.id per-event so batched entries stay correct.
        for event in entry.get("messaging", []):
            message = event.get("message")
            if not isinstance(message, dict):
                continue  # read / delivery / postback / reaction — no message

            if message.get("is_echo"):
                continue  # our own outbound, echoed back — never reply to it

            sender_id = str(event.get("sender", {}).get("id", ""))
            recipient_id = str(event.get("recipient", {}).get("id", "") or entry.get("id", ""))
            mid = str(message.get("mid", ""))
            if not sender_id or not mid:
                continue

            text = message.get("text")
            # Attachments (image/audio/share/story reply) arrive under
            # message.attachments with no text; classify by the first type.
            msg_type = "text"
            if text is None:
                attachments = message.get("attachments") or []
                if attachments:
                    msg_type = attachments[0].get("type", "unknown")

            messages.append(
                InboundMessage(
                    external_message_id=mid,
                    external_event_id=mid,
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    text=text,
                    message_type=msg_type,
                    raw=event,
                )
            )

    return messages


def is_status_only(payload: dict[str, Any]) -> bool:
    """True if the payload carries no actionable inbound message.

    (Only echoes, read/delivery receipts, reactions, etc.)
    """
    return not parse_webhook(payload)
