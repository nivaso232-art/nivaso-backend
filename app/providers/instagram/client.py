"""Instagram API (with Instagram Login) — sending DMs.

Send-only client, mirroring ``app.providers.whatsapp.client``. Receiving is
handled by the webhook handler + channel parser. Retries transient (5xx /
network) errors via tenacity; a 4xx from Meta (bad token, outside the 24h
messaging window) is surfaced immediately, not retried.

Instagram messaging uses ``graph.instagram.com`` and the ``/me/messages``
Send API — different host and payload shape from WhatsApp Cloud API. The
per-business access token is passed at construction so each business replies
from its own IG account.
"""

from __future__ import annotations

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.errors import ProviderError

log = structlog.get_logger(__name__)


class InstagramClient:
    """Send text DMs via the Instagram Send API."""

    def __init__(self, *, access_token: str | None = None) -> None:
        self._access_token = access_token
        if not self._access_token:
            raise ProviderError(
                "Instagram is not configured (no access_token for this channel)"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    async def send_text(self, *, to: str, text: str) -> str:
        """Send a plain-text DM and return the provider message id.

        Args:
            to: The recipient's IGSID (Instagram-scoped user id) from the
                inbound webhook's ``sender.id``.
            text: The message body, max 1000 characters for IG DMs.
        """
        url = f"{settings.instagram_graph_base_url}/me/messages"
        payload = {
            "recipient": {"id": to},
            "message": {"text": text},
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
                        f"Instagram send failed (server): {exc.response.text}",
                        details={"status": exc.response.status_code},
                    ) from exc
                raise ProviderError(
                    f"Instagram send failed (client): {exc.response.text}",
                    details={"status": exc.response.status_code, "to": to},
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(f"Instagram request error: {exc}") from exc

        data = resp.json()
        mid: str = data.get("message_id", "")
        log.info("instagram_message_sent", to=to, message_id=mid)
        return mid
