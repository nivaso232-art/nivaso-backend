"""Fulfillment lifecycle.

**Rule 3**: no agent tool reaches this module. The AI can tell a customer their
order is being prepared; it cannot declare it delivered. ``mark_delivered`` is
for the webhook/admin path and a human agent's console action.

**Rule 10**: nothing here handles credentials. ``metadata["credential_ref"]``
is a handle into a real secrets manager. Whatever the business actually hands
over - a game account, a booking code, a download link - lives there, with
encryption, access control, retention limits, and an audit log. Not in this
table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.enums import FulfillmentStatus, OrderStatus
from app.models.fulfillment import Fulfillment
from app.models.order import Order
from app.repositories.fulfillments import FulfillmentRepository
from app.repositories.orders import OrderRepository
from app.services.state_machine import (
    assert_fulfillment_transition,
    assert_order_transition,
)

log = structlog.get_logger(__name__)

# Keys that must never appear in fulfillment metadata. Checked rather than
# trusted: the temptation to stash "just the password, temporarily" is exactly
# how plaintext credentials end up in a widely-joined table.
_FORBIDDEN_METADATA_KEYS = frozenset(
    {"password", "passwd", "otp", "pin", "secret", "token", "credentials", "cvv"}
)


class FulfillmentService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.fulfillments = FulfillmentRepository(session, business_id)
        self.orders = OrderRepository(session, business_id)

    async def create_for_paid_order(self, order: Order) -> Fulfillment:
        """Open a fulfillment record once payment is confirmed.

        Called from the payment webhook path, right after the order becomes
        PAID - never from an agent tool.
        """
        if not order.is_paid:
            raise ConflictError(
                "Fulfillment can only be created for a paid order.",
                details={"reference": order.reference, "status": order.status.value},
            )

        existing = await self.fulfillments.get_for_order(order.id)
        if existing is not None:
            return existing

        fulfillment = Fulfillment(
            order_id=order.id,
            status=FulfillmentStatus.PENDING,
        )
        await self.fulfillments.add(fulfillment)

        log.info(
            "fulfillment_created",
            fulfillment_id=str(fulfillment.id),
            reference=order.reference,
        )
        return fulfillment

    async def mark_ready(
        self, fulfillment: Fulfillment, *, metadata: dict[str, Any] | None = None
    ) -> Fulfillment:
        """Deliverable is prepared and waiting to go out."""
        assert_fulfillment_transition(fulfillment.status, FulfillmentStatus.READY)
        fulfillment.status = FulfillmentStatus.READY
        if metadata:
            fulfillment.metadata_ = {
                **fulfillment.metadata_,
                **self._sanitize_metadata(metadata),
            }
        await self.session.flush()
        return fulfillment

    async def mark_delivered(
        self,
        fulfillment: Fulfillment,
        *,
        delivered_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Fulfillment:
        """Customer has it. Also closes out the order.

        Not agent-reachable (rule 3): a customer saying "got it, thanks" is not
        proof of delivery, and an AI that can close orders can be talked into
        closing one that never arrived.
        """
        assert_fulfillment_transition(fulfillment.status, FulfillmentStatus.DELIVERED)
        fulfillment.status = FulfillmentStatus.DELIVERED
        fulfillment.fulfilled_at = datetime.now(timezone.utc)

        merged: dict[str, Any] = dict(fulfillment.metadata_)
        if metadata:
            merged.update(self._sanitize_metadata(metadata))
        if delivered_by:
            merged["delivered_by"] = delivered_by
        fulfillment.metadata_ = merged

        order = await self.orders.get_or_raise(fulfillment.order_id)
        if order.status is not OrderStatus.FULFILLED:
            assert_order_transition(order.status, OrderStatus.FULFILLED)
            order.status = OrderStatus.FULFILLED

        await self.session.flush()

        log.info(
            "fulfillment_delivered",
            fulfillment_id=str(fulfillment.id),
            reference=order.reference,
        )
        return fulfillment

    async def mark_failed(
        self, fulfillment: Fulfillment, *, reason: str
    ) -> Fulfillment:
        assert_fulfillment_transition(fulfillment.status, FulfillmentStatus.FAILED)
        fulfillment.status = FulfillmentStatus.FAILED
        fulfillment.notes = reason
        await self.session.flush()

        log.warning(
            "fulfillment_failed",
            fulfillment_id=str(fulfillment.id),
            reason=reason,
        )
        return fulfillment

    async def status_for_order(self, order_id: uuid.UUID) -> Fulfillment | None:
        """Read-only. This *is* agent-reachable via ``get_order_status`` - the
        AI may report progress, it just cannot change it."""
        return await self.fulfillments.get_for_order(order_id)

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """Strip anything that looks like a credential (rule 10)."""
        clean: dict[str, Any] = {}
        for key, value in metadata.items():
            if key.lower() in _FORBIDDEN_METADATA_KEYS:
                log.error(
                    "fulfillment_metadata_rejected",
                    key=key,
                    hint="store secrets in a secrets manager and keep only a "
                    "credential_ref handle here",
                )
                continue
            clean[key] = value
        return clean
