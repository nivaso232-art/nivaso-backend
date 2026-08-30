"""WhatsApp Cloud API webhook payload parser.

Meta delivers a batch envelope — one POST can contain messages from
multiple numbers. We unpack it into individual ``InboundMessage`` objects,
one per message, which the webhook handler processes independently.

Reference:
    https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InboundMessage:
    """Everything extracted from one WhatsApp message."""

    external_message_id: str    # wamid — used for message-level idempotency
    external_event_id: str      # synthesised from wamid; used for webhook_events
    wa_id: str                  # sender's phone number / wa_id
    display_name: str | None    # from the contacts block; may be absent
    text: str | None            # None for non-text message types
    message_type: str           # text | image | audio | video | document | …
    raw: dict[str, Any]         # verbatim message object for payload storage


def parse_webhook(payload: dict[str, Any]) -> list[InboundMessage]:
    """Extract inbound messages from a Meta webhook payload.

    Returns an empty list for non-message events (delivery receipts, read
    receipts, etc.) so the handler can call ``mark_ignored`` on those.
    """
    messages: list[InboundMessage] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue

            value = change.get("value", {})

            # Build a wa_id → display_name lookup from the contacts block.
            contacts: dict[str, str | None] = {
                c["wa_id"]: c.get("profile", {}).get("name")
                for c in value.get("contacts", [])
            }

            for msg in value.get("messages", []):
                wa_id: str = msg.get("from", "")
                wamid: str = msg.get("id", "")
                msg_type: str = msg.get("type", "text")

                text: str | None = None
                if msg_type == "text":
                    text = msg.get("text", {}).get("body")

                messages.append(
                    InboundMessage(
                        external_message_id=wamid,
                        external_event_id=wamid,
                        wa_id=wa_id,
                        display_name=contacts.get(wa_id),
                        text=text,
                        message_type=msg_type,
                        raw=msg,
                    )
                )

    return messages


def is_status_only(payload: dict[str, Any]) -> bool:
    """Return True if the payload contains only delivery/read status updates."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "messages":
                if change.get("value", {}).get("messages"):
                    return False
    return True
