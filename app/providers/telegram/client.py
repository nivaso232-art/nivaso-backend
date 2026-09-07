"""Telegram Bot API — sending messages and webhook registration.

Per-business bot tokens are passed at construction time so each business
can use its own Telegram bot. Falls back to global settings when not provided.
"""

from __future__ import annotations

from typing import Any

import structlog
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.errors import ProviderError

log = structlog.get_logger(__name__)

_BASE = "https://api.telegram.org"


class TelegramClient:
    """Send messages and manage webhooks via the Telegram Bot API."""

    def __init__(self, *, bot_token: str | None = None) -> None:
        token = bot_token or settings.telegram_bot_token
        if not token:
            raise ProviderError(
                "Telegram is not configured (TELEGRAM_BOT_TOKEN missing)"
            )
        self._base = f"{_BASE}/bot{token}"

    async def set_webhook(self, *, url: str, secret_token: str | None = None) -> None:
        """Register this bot's webhook URL with Telegram."""
        payload: dict[str, Any] = {"url": url}
        if secret_token:
            payload["secret_token"] = secret_token
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(f"{self._base}/setWebhook", json=payload)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ProviderError(
                    f"Telegram setWebhook failed: {exc.response.text}",
                    details={"status": exc.response.status_code},
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(f"Telegram request error: {exc}") from exc
        data = resp.json()
        if not data.get("ok"):
            raise ProviderError(
                f"Telegram setWebhook rejected: {data.get('description', 'unknown')}",
                details=data,
            )
        log.info("telegram_webhook_registered", url=url)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    async def send_message(self, *, chat_id: str | int, text: str) -> int:
        """Send a text message and return Telegram's ``message_id``."""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{self._base}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    raise ProviderError(
                        f"Telegram send failed (server): {exc.response.text}",
                        details={"status": exc.response.status_code},
                    ) from exc
                raise ProviderError(
                    f"Telegram send failed (client): {exc.response.text}",
                    details={"status": exc.response.status_code},
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(f"Telegram request error: {exc}") from exc

        data = resp.json()
        message_id: int = data["result"]["message_id"]
        log.info("telegram_message_sent", chat_id=chat_id, message_id=message_id)
        return message_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    async def send_with_keyboard(
        self, *, chat_id: str | int, text: str, buttons: list[dict]
    ) -> int:
        """Send a message with an inline keyboard.

        Args:
            chat_id: Telegram chat id.
            text: Message text.
            buttons: List of dicts with ``id`` and ``label`` keys.
                     Buttons are arranged in rows of up to 3 per row.

        Returns:
            Telegram ``message_id``.
        """
        # Build inline keyboard: rows of 2-3 buttons
        keyboard_buttons = [
            {
                "text": str(btn.get("label", btn.get("title", f"Option {i+1}")))[:64],
                "callback_data": str(btn.get("id", str(i)))[:64],
            }
            for i, btn in enumerate(buttons)
        ]

        # Arrange into rows of up to 3
        row_size = 2 if len(keyboard_buttons) > 3 else len(keyboard_buttons)
        rows = [
            keyboard_buttons[i : i + row_size]
            for i in range(0, len(keyboard_buttons), row_size)
        ]

        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"inline_keyboard": rows},
        }

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{self._base}/sendMessage",
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    raise ProviderError(
                        f"Telegram send_with_keyboard failed (server): {exc.response.text}",
                        details={"status": exc.response.status_code},
                    ) from exc
                raise ProviderError(
                    f"Telegram send_with_keyboard failed (client): {exc.response.text}",
                    details={"status": exc.response.status_code},
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(f"Telegram request error: {exc}") from exc

        data = resp.json()
        message_id: int = data["result"]["message_id"]
        log.info(
            "telegram_keyboard_message_sent", chat_id=chat_id, message_id=message_id
        )
        return message_id
