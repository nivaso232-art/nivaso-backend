"""Razorpay REST client.

Wraps the Payment Links API. Flow:
  1. ``create_payment_link`` -> gives the customer a URL to pay
  2. Razorpay calls our webhook when the customer pays
  3. ``parse_webhook_outcome`` turns that payload into a ``ProviderOutcome``

The agent only ever does step 1. The webhook handler does step 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.errors import ProviderError
from app.models.enums import PaymentProvider, PaymentStatus
from app.services.payment_service import ProviderOutcome

log = structlog.get_logger(__name__)

_BASE = "https://api.razorpay.com/v1"


@dataclass(frozen=True)
class PaymentLinkResult:
    link_id: str    # plink_xxx — used to match the webhook to our payment row
    short_url: str  # the URL we send to the customer


class RazorpayClient:
    """Thin async wrapper around the Razorpay Payment Links API."""

    def __init__(
        self,
        *,
        key_id: str | None = None,
        key_secret: str | None = None,
    ) -> None:
        k = key_id or settings.razorpay_key_id
        s = key_secret or settings.razorpay_key_secret
        if not k or not s:
            raise ProviderError(
                "Razorpay credentials not configured "
                "(RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET missing)"
            )
        self._auth = (k, s)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def create_payment_link(
        self,
        *,
        amount_minor: int,
        currency: str,
        reference: str,
        description: str,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> PaymentLinkResult:
        """Create a Razorpay Payment Link and return its id and short URL.

        Args:
            amount_minor: Amount in the currency's smallest unit (paise for INR).
            reference: The order reference, stored as ``reference_id`` so it
                appears in the Razorpay dashboard alongside our reference.
        """
        payload: dict[str, Any] = {
            "amount": amount_minor,
            "currency": currency,
            "description": description,
            "reference_id": reference,
            "upi_link": False,
        }
        if customer_name or customer_phone:
            payload["customer"] = {}
            if customer_name:
                payload["customer"]["name"] = customer_name
            if customer_phone:
                payload["customer"]["contact"] = customer_phone

        async with httpx.AsyncClient(auth=self._auth, timeout=30) as client:
            try:
                resp = await client.post(f"{_BASE}/payment_links", json=payload)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ProviderError(
                    f"Razorpay payment link creation failed: {exc.response.text}",
                    details={"status_code": exc.response.status_code},
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(f"Razorpay request failed: {exc}") from exc

        data = resp.json()
        log.info(
            "razorpay_payment_link_created",
            link_id=data.get("id"),
            reference=reference,
            amount=amount_minor,
        )
        return PaymentLinkResult(
            link_id=data["id"],
            short_url=data["short_url"],
        )


def parse_webhook_outcome(payload: dict[str, Any]) -> ProviderOutcome | None:
    """Turn a verified Razorpay webhook payload into a ``ProviderOutcome``.

    Returns ``None`` for events we do not act on (subscription events,
    disputes) so the handler can mark them ``IGNORED`` instead of failing.
    """
    event = payload.get("event", "")
    entities = payload.get("payload", {})

    if event == "payment_link.paid":
        plink = entities.get("payment_link", {}).get("entity", {})
        payment = entities.get("payment", {}).get("entity", {})
        return ProviderOutcome(
            provider=PaymentProvider.RAZORPAY,
            provider_payment_id=payment.get("id", ""),
            provider_payment_link_id=plink.get("id"),
            status=PaymentStatus.SUCCESS,
            amount=Decimal(str(payment.get("amount", 0))) / 100,
            currency=payment.get("currency", "INR"),
            raw_payload=payload,
        )

    if event in ("payment.failed",):
        payment = entities.get("payment", {}).get("entity", {})
        return ProviderOutcome(
            provider=PaymentProvider.RAZORPAY,
            provider_payment_id=payment.get("id", ""),
            provider_payment_link_id=payment.get("payment_link_id"),
            status=PaymentStatus.FAILED,
            failure_reason=payment.get("error_description"),
            raw_payload=payload,
        )

    return None
