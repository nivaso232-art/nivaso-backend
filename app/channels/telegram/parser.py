"""Telegram Bot API webhook payload parser.

Telegram sends one Update object per POST. We extract what we need
into an ``InboundMessage`` and return ``None`` for non-message updates
(inline queries, callback queries, etc.) so the handler can ignore them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InboundMessage:
    """Everything extracted from one Telegram update."""

    external_message_id: str    # str(message.message_id)
    external_event_id: str      # str(update_id)
    chat_id: str                # str(message.chat.id)
    display_name: str | None    # first_name + last_name, or None
    text: str | None
    message_type: str           # text | photo | video | audio | document | …
    raw: dict[str, Any]         # verbatim message object


def parse_update(payload: dict[str, Any]) -> InboundMessage | None:
    """Parse a Telegram Update. Returns ``None`` for irrelevant update types."""
    update_id = str(payload.get("update_id", ""))
    message = payload.get("message") or payload.get("edited_message")
    if not message:
        return None

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    from_user = message.get("from", {})

    first = (from_user.get("first_name") or "").strip()
    last = (from_user.get("last_name") or "").strip()
    display_name = (f"{first} {last}").strip() or None

    text: str | None = message.get("text") or message.get("caption")
    msg_type = "text" if text else _infer_type(message)

    return InboundMessage(
        external_message_id=str(message.get("message_id", update_id)),
        external_event_id=update_id,
        chat_id=chat_id,
        display_name=display_name,
        text=text,
        message_type=msg_type,
        raw=message,
    )


def _infer_type(message: dict[str, Any]) -> str:
    for key in (
        "photo", "video", "audio", "voice", "document",
        "sticker", "location", "contact", "animation",
    ):
        if key in message:
            return key
    return "unknown"
