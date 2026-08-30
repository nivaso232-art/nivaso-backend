"""Human-readable reference generators.

UUIDs are the primary keys; these are the strings a customer reads back over
WhatsApp ("bro ORD-2608-7F3K9Q payment aagala"). Format:

    ORD-2608-7F3K9Q
    ^^^ ^^^^ ^^^^^^
     |   |     `-- 6 chars of Crockford base32 from a CSPRNG
     |   `-------- yymm, so support can date an order at a glance
     `------------ entity prefix

Why random rather than a per-business counter: a counter table serialises
order creation per tenant (every INSERT waits on the same row lock) and a
sequential public id leaks how much business a tenant is doing. 6 base32 chars
is ~1.07e9 values per month per prefix; collisions are handled by the caller
retrying against the unique index.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

# Crockford base32: no I, L, O or U - avoids 1/I/L and 0/O confusion when a
# customer reads a reference aloud or types it back.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SUFFIX_LEN = 6

ORDER_PREFIX = "ORD"
TICKET_PREFIX = "TKT"
PAYMENT_PREFIX = "PAY"


def _suffix(length: int = _SUFFIX_LEN) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def generate_reference(prefix: str, *, now: datetime | None = None) -> str:
    """Build a reference such as ``ORD-2608-7F3K9Q``."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%y%m")
    return f"{prefix}-{stamp}-{_suffix()}"


def generate_order_reference(now: datetime | None = None) -> str:
    return generate_reference(ORDER_PREFIX, now=now)


def generate_ticket_reference(now: datetime | None = None) -> str:
    return generate_reference(TICKET_PREFIX, now=now)


def normalize_reference(value: str) -> str:
    """Canonicalise a reference a customer typed by hand.

    Lowercase, spaces and the classic base32 lookalikes are all forgiven so
    "ord 2608 7f3k9q" and "0RD-26O8-7F3K9Q" both resolve.
    """
    cleaned = value.strip().upper().replace(" ", "-").replace("_", "-")
    head, _, tail = cleaned.partition("-")
    # Only fix lookalikes in the random tail; the prefix is alphabetic already.
    tail = tail.replace("I", "1").replace("L", "1").replace("O", "0").replace("U", "V")
    return f"{head}-{tail}" if tail else head
