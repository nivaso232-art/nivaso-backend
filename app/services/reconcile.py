"""Payment reconciliation — settle an order by polling the provider.

The webhook is the primary path to PAID, but webhooks get missed (no public URL
in local dev, transient delivery failures in prod). Reconciliation asks Razorpay
directly whether a link was paid and, if so, applies the same outcome the webhook
would have — then delivers and notifies. An authenticated provider API response
is as authoritative as a signed webhook; neither is the customer's word, so this
does not weaken rule 2.

Manages its own UnitOfWork blocks, which behave correctly whether called with an
ambient transaction (inside the agent turn) or without (an admin/cron caller).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.uow import UnitOfWork
from app.models.enums import PaymentProvider, PaymentStatus
from app.models.order import Order
from app.providers.razorpay.client import RazorpayClient
from app.repositories.payments import PaymentRepository
from app.services.delivery_service import (
    DeliveredItem,
    DeliveryService,
    format_delivery_message,
)
from app.services.notify import send_to_customer
from app.services.payment_service import PaymentService, ProviderOutcome

log = structlog.get_logger(__name__)


@dataclass
class ReconcileResult:
    reference: str
    paid: bool = False
    newly_paid: bool = False
    delivered: bool = False
    out_of_stock: bool = False
    items: list[DeliveredItem] = field(default_factory=list)


async def reconcile_and_deliver(
    session: AsyncSession, business_id: uuid.UUID, order: Order
) -> ReconcileResult:
    """Poll Razorpay for this order's link; if paid, settle and deliver."""
    if not order.is_paid:
        if settings.payments_mock:
            return ReconcileResult(order.reference)

        payment = await PaymentRepository(session, business_id).get_open_for_order(
            order.id
        )
        if (
            payment is None
            or payment.provider is not PaymentProvider.RAZORPAY
            or not payment.provider_payment_link_id
        ):
            return ReconcileResult(order.reference)

        link = await RazorpayClient().fetch_payment_link(
            payment.provider_payment_link_id
        )
        if not link.is_paid:
            return ReconcileResult(order.reference)

        outcome = ProviderOutcome(
            provider=PaymentProvider.RAZORPAY,
            provider_payment_id=link.provider_payment_id
            or f"reconciled_{uuid.uuid4().hex[:12]}",
            provider_payment_link_id=payment.provider_payment_link_id,
            status=PaymentStatus.SUCCESS,
            amount=Decimal(link.amount_paid_minor) / 100,
            currency=link.currency,
            raw_payload={"reconciled": True},
        )
        async with UnitOfWork(session):
            result = await PaymentService(session, business_id).apply_provider_outcome(
                outcome
            )
        order = result.order
        log.info(
            "payment_reconciled",
            reference=order.reference,
            newly_paid=result.order_status_changed,
        )
        newly_paid = result.order_status_changed
    else:
        newly_paid = False

    if not order.is_paid:
        return ReconcileResult(order.reference)

    async with UnitOfWork(session):
        delivery = await DeliveryService(session, business_id).deliver_for_order(order)

    if delivery.delivered and delivery.items and not delivery.already_delivered:
        text = format_delivery_message(delivery.order_reference, delivery.items)
        await send_to_customer(session, business_id, order.customer_id, text)

    return ReconcileResult(
        reference=order.reference,
        paid=True,
        newly_paid=newly_paid,
        delivered=delivery.delivered,
        out_of_stock=delivery.out_of_stock,
        items=delivery.items,
    )
