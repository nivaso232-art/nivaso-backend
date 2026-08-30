"""HMAC webhook signature verification.

Every comparison uses :func:`hmac.compare_digest`. A naive ``==`` on a
signature is timing-attackable, and these endpoints are the only thing
standing between the internet and "this order is paid".
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.errors import SignatureError


def _digest(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_hmac_sha256(
    *,
    secret: str,
    payload: bytes,
    provided: str | None,
    prefix: str = "",
    source: str = "webhook",
) -> None:
    """Verify a hex-encoded HMAC-SHA256 signature or raise.

    Args:
        secret: Shared secret from provider config.
        payload: The **raw** request body. Never re-serialise the parsed JSON
            before hashing - key order and whitespace change the digest.
        provided: Signature header value as received.
        prefix: Header prefix to strip, e.g. ``"sha256="`` for Meta.
        source: Label used in the raised error.
    """
    if not secret:
        raise SignatureError(
            f"{source}: signing secret is not configured", details={"source": source}
        )
    if not provided:
        raise SignatureError(
            f"{source}: signature header missing", details={"source": source}
        )

    candidate = provided.removeprefix(prefix).strip() if prefix else provided.strip()
    expected = _digest(secret, payload)

    if not hmac.compare_digest(candidate, expected):
        raise SignatureError(
            f"{source}: signature mismatch", details={"source": source}
        )


def verify_meta_signature(
    *, app_secret: str, payload: bytes, header: str | None
) -> None:
    """WhatsApp Cloud API: ``X-Hub-Signature-256: sha256=<hex>``."""
    verify_hmac_sha256(
        secret=app_secret,
        payload=payload,
        provided=header,
        prefix="sha256=",
        source="whatsapp",
    )


def verify_razorpay_signature(
    *, webhook_secret: str, payload: bytes, header: str | None
) -> None:
    """Razorpay: ``X-Razorpay-Signature: <hex>`` (no prefix)."""
    verify_hmac_sha256(
        secret=webhook_secret,
        payload=payload,
        provided=header,
        source="razorpay",
    )


def verify_telegram_secret(*, expected: str, header: str | None) -> None:
    """Telegram sends back a fixed secret token, not an HMAC of the body.

    Set it when registering the webhook via ``setWebhook?secret_token=...``.
    """
    if not expected:
        raise SignatureError(
            "telegram: webhook secret is not configured",
            details={"source": "telegram"},
        )
    if not header or not hmac.compare_digest(header, expected):
        raise SignatureError(
            "telegram: secret token mismatch", details={"source": "telegram"}
        )


def verify_internal_api_key(*, expected: str, provided: str | None) -> None:
    """Guard for admin/internal routes (``X-Internal-Key``)."""
    if not provided or not hmac.compare_digest(provided, expected):
        raise SignatureError("invalid internal API key", details={"source": "internal"})
