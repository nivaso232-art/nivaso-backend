"""Mock payment page — stands in for Razorpay when it's unavailable (KYC).

``GET /mock/pay/{slug}/{reference}`` marks the order paid (through the real
payment path), triggers credential delivery, notifies the customer, and shows a
confirmation page. Only mounted when ``PAYMENTS_MOCK=true``.
"""

from __future__ import annotations

import html
import uuid

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import settings
from app.core.uow import UnitOfWork
from app.models.enums import PaymentStatus
from app.repositories.businesses import BusinessRepository
from app.repositories.orders import OrderRepository
from app.repositories.payments import PaymentRepository
from app.services.delivery_service import DeliveryService, format_delivery_message
from app.services.notify import send_to_customer
from app.services.payment_service import PaymentService, ProviderOutcome

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/mock", tags=["mock"])


def _page(title: str, body: str, *, status_code: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1115;
   color:#e8eaed;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
 .card{{background:#1a1d24;border:1px solid #2a2f3a;border-radius:16px;padding:32px;max-width:440px;width:90%}}
 h1{{font-size:20px;margin:0 0 6px}} .muted{{color:#9aa0ab;font-size:14px}}
 .cred{{background:#11141a;border:1px solid #2a2f3a;border-radius:10px;padding:14px;margin:14px 0;font-size:14px}}
 .cred b{{color:#7dd3a0}} .row{{margin:4px 0}} code{{color:#e8eaed}}
 .badge{{display:inline-block;background:#14432b;color:#7dd3a0;border-radius:999px;padding:4px 12px;font-size:12px;margin-bottom:14px}}
</style></head><body><div class="card">{body}</div></body></html>"""
    return HTMLResponse(doc, status_code=status_code)


@router.get("/pay/{slug}/{reference}")
async def mock_pay(
    slug: str,
    reference: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if not settings.payments_mock:
        return _page("Unavailable", "<h1>Mock payments are disabled.</h1>", status_code=404)

    business = await BusinessRepository(session).get_active_or_raise(slug)
    order = await OrderRepository(session, business.id).get_by_reference(reference)
    if order is None:
        return _page(
            "Not found",
            f"<h1>Order not found</h1><p class='muted'>{html.escape(reference)}</p>",
            status_code=404,
        )

    payments = PaymentRepository(session, business.id)
    payment = await payments.get_open_for_order(order.id)
    if payment is None:
        payment = await payments.get_successful_for_order(order.id)
    if payment is None or not payment.provider_payment_link_id:
        return _page(
            "No payment link",
            "<h1>This order has no payment link.</h1>",
            status_code=400,
        )

    outcome = ProviderOutcome(
        provider=payment.provider,
        provider_payment_id=payment.provider_payment_id or f"mockpay_{uuid.uuid4().hex[:14]}",
        provider_payment_link_id=payment.provider_payment_link_id,
        status=PaymentStatus.SUCCESS,
        amount=order.total,
        currency=order.currency,
        raw_payload={"mock": True},
    )

    async with UnitOfWork(session):
        result = await PaymentService(session, business.id).apply_provider_outcome(outcome)
    async with UnitOfWork(session):
        delivery = await DeliveryService(session, business.id).deliver_for_order(result.order)

    if delivery.delivered and delivery.items:
        text = format_delivery_message(delivery.order_reference, delivery.items)
        await send_to_customer(session, business.id, order.customer_id, text)

    # Build the confirmation page.
    amount = f"{order.currency} {order.total}"
    if delivery.out_of_stock:
        body = (
            "<span class='badge'>Payment received</span>"
            f"<h1>Paid — {html.escape(amount)}</h1>"
            f"<p class='muted'>Order {html.escape(order.reference)}</p>"
            "<p>Your login is being prepared and a team member will send it "
            "shortly.</p>"
        )
    else:
        creds_html = "".join(
            "<div class='cred'>"
            f"<div class='row'><b>{html.escape(i.product_name)}</b></div>"
            f"<div class='row'>ID: <code>{html.escape(i.username)}</code></div>"
            f"<div class='row'>Password: <code>{html.escape(i.password)}</code></div>"
            "</div>"
            for i in delivery.items
        )
        note = " (already delivered)" if delivery.already_delivered else ""
        body = (
            "<span class='badge'>Payment successful</span>"
            f"<h1>Paid — {html.escape(amount)}</h1>"
            f"<p class='muted'>Order {html.escape(order.reference)}{note}</p>"
            "<p>Here are your game login details:</p>"
            f"{creds_html}"
            "<p class='muted'>Keep these safe. Enjoy! 🎮</p>"
        )
    log.info("mock_payment_completed", reference=order.reference, delivered=delivery.delivered)
    return _page("Payment", body)
