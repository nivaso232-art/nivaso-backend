"""Mock payment links, for when the real provider is unavailable (e.g. KYC).

Produces the same ``PaymentLinkResult`` shape as the Razorpay client, but the
URL points at this app's ``/mock/pay/{slug}/{reference}`` page — opening it
completes the payment and triggers delivery. Gated by ``PAYMENTS_MOCK``.
"""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.providers.razorpay.client import PaymentLinkResult


def mock_payment_link(*, slug: str, reference: str) -> PaymentLinkResult:
    base = settings.public_base_url.rstrip("/")
    return PaymentLinkResult(
        link_id=f"mock_{uuid.uuid4().hex[:16]}",
        short_url=f"{base}/mock/pay/{slug}/{reference}",
    )
