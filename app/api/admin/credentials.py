"""Admin API — credential vault + a test hook to simulate a paid order.

Routes require the X-Internal-Key header (enforced at the router level).
``{slug}`` is the business slug.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.models.business import Business
from app.models.enums import PaymentProvider, PaymentStatus
from app.repositories.orders import OrderRepository
from app.repositories.payments import PaymentRepository
from app.repositories.products import ProductRepository
from app.services.credential_service import CredentialService
from app.services.delivery_service import DeliveryService
from app.services.payment_service import PaymentService, ProviderOutcome

router = APIRouter(prefix="/{slug}", tags=["admin:credentials"])


def _uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(
            f"{field} must be a valid UUID.", details={field: value}
        ) from exc


# -- credential pool ----------------------------------------------------------

class AddCredentialIn(BaseModel):
    username: str
    password: str
    capacity: int = 1
    label: str | None = None


def _cred_out(c: Any) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "product_id": str(c.product_id),
        "username": c.username,
        "label": c.label,
        "capacity": c.capacity,
        "allocated": c.allocated,
        "status": c.status.value,
    }


@router.post(
    "/products/{product_id}/credentials", status_code=status.HTTP_201_CREATED
)
async def add_credential(
    slug: str,
    product_id: str,
    body: AddCredentialIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    pid = _uuid(product_id, "product_id")
    await ProductRepository(session, business.id).get_or_raise(pid)  # tenant + exists
    async with UnitOfWork(session):
        cred = await CredentialService(session, business.id).add_credential(
            product_id=pid,
            username=body.username,
            password=body.password,
            capacity=body.capacity,
            label=body.label,
        )
    return _cred_out(cred)


@router.get("/products/{product_id}/credentials")
async def list_credentials(
    slug: str,
    product_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """List pool entries. Secrets are never returned — only usage stats."""
    pid = _uuid(product_id, "product_id")
    creds = await CredentialService(session, business.id).list_for_product(pid)
    return [_cred_out(c) for c in creds]


# -- test hook: simulate a successful payment + run delivery ------------------

@router.post("/orders/{reference}/simulate-payment")
async def simulate_payment(
    slug: str,
    reference: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """TEST ONLY: pretend the customer paid, then run credential delivery.

    Reuses the real payment path (``apply_provider_outcome``) by synthesising a
    Razorpay ``payment_link.paid`` outcome against the order's existing payment
    link, so the flow matches production. Returns the delivered credentials so
    you can verify without a live WhatsApp channel.
    """
    order = await OrderRepository(session, business.id).get_by_reference(reference)
    if order is None:
        raise NotFoundError("Order not found", details={"reference": reference})

    payments = PaymentRepository(session, business.id)
    payment = await payments.get_open_for_order(order.id)
    if payment is None:
        payment = await payments.get_successful_for_order(order.id)
    if payment is None or not payment.provider_payment_link_id:
        raise ValidationError(
            "This order has no payment link yet — ask the agent to create one "
            "first (send a 'buy' message).",
            details={"reference": reference},
        )

    outcome = ProviderOutcome(
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id=payment.provider_payment_id or f"pay_sim_{uuid.uuid4().hex[:14]}",
        provider_payment_link_id=payment.provider_payment_link_id,
        status=PaymentStatus.SUCCESS,
        amount=order.total,
        currency=order.currency,
        raw_payload={"simulated": True},
    )

    # PAID and delivery in separate transactions: an out-of-stock delivery must
    # not roll back the payment.
    async with UnitOfWork(session):
        result = await PaymentService(session, business.id).apply_provider_outcome(outcome)

    async with UnitOfWork(session):
        delivery = await DeliveryService(session, business.id).deliver_for_order(
            result.order
        )

    return {
        "order_reference": delivery.order_reference,
        "delivered": delivery.delivered,
        "already_delivered": delivery.already_delivered,
        "out_of_stock": delivery.out_of_stock,
        "missing_products": delivery.missing_products,
        "credentials": [
            {"product": i.product_name, "id": i.username, "password": i.password}
            for i in delivery.items
        ],
    }
