"""WhatsApp Cloud API — sending messages.

This client only sends. Receiving is handled by the webhook handler and
the channel parser. Retry is via tenacity on transient errors; a 4xx
from Meta (bad token, invalid phone) is not retried.

Per-business credentials are passed at construction time so multi-tenant
deployments can use different phone numbers and access tokens for each
business. Falls back to global settings when not provided.
"""

from __future__ import annotations

import structlog
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.errors import ProviderError

log = structlog.get_logger(__name__)


class WhatsAppClient:
    """Send text messages via the WhatsApp Cloud API."""

    def __init__(
        self,
        *,
        phone_number_id: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self._phone_number_id = phone_number_id or settings.whatsapp_phone_number_id
        self._access_token = access_token or settings.whatsapp_access_token
        if not self._phone_number_id or not self._access_token:
            raise ProviderError(
                "WhatsApp is not configured "
                "(WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID missing)"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    async def send_text(self, *, to: str, text: str) -> str:
        """Send a plain-text message and return the provider message id (wamid).

        Args:
            to: The recipient's wa_id — the numeric phone number without the
                leading plus sign, e.g. ``"919876543210"``.
            text: The message body, max 4096 characters.
        """
        url = (
            f"{settings.whatsapp_graph_base_url}"
            f"/{self._phone_number_id}/messages"
        )
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        }
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    raise ProviderError(
                        f"WhatsApp send failed (server): {exc.response.text}",
                        details={"status": exc.response.status_code},
                    ) from exc
                raise ProviderError(
                    f"WhatsApp send failed (client): {exc.response.text}",
                    details={"status": exc.response.status_code, "to": to},
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(f"WhatsApp request error: {exc}") from exc

        data = resp.json()
        wamid: str = data.get("messages", [{}])[0].get("id", "")
        log.info("whatsapp_message_sent", to=to, wamid=wamid)
        return wamid
