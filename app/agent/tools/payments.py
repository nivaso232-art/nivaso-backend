"""Payment tools: ``create_payment_link`` and ``check_payment_status``.

The tools the agent does *not* have here are the point. There is no
``mark_paid``, no ``confirm_payment``, no ``verify_payment``. The only
mutating operation exposed is "open a PENDING attempt and give the customer a
link", and the amount for that comes from ``order.total``.

So when a customer says "bro I paid already", the best the agent can do is
call ``check_payment_status`` and read back what the provider actually
reported. If Razorpay has not sent a verified webhook, the order is not paid -
regardless of how insistent the customer is (rule 2).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, schema, string_prop
from app.core.config import settings
from app.core.errors import ProviderError
from app.core.ids import normalize_reference
from app.models.enums import ConversationState, OrderStatus, PaymentProvider
from app.providers.mock_payments import mock_payment_link
from app.providers.razorpay.client import RazorpayClient
from app.repositories.business_channels import BusinessChannelRepository

log = structlog.get_logger(__name__)


async def create_payment_link(
    ctx: ToolContext, order_reference: str
) -> dict[str, Any]:
    """Issue a payment link for an order.

    The amount is read from the order, which was itself priced from the
    catalog. No amount parameter exists on this tool.

    Returns a success dict with payment_url, or an error dict with
    retry_possible so the model knows whether to apologise-and-retry or
    escalate to a human. Returning an error dict (rather than raising)
    keeps is_error=False, which prevents the model from immediately
    creating a support ticket for transient provider issues.
    """
    order = await ctx.orders.resolve_order(
        customer_id=ctx.customer_id,
        reference=normalize_reference(order_reference),
    )

    if settings.payments_mock:
        link = mock_payment_link(slug=ctx.business.slug, reference=order.reference)
        provider = PaymentProvider.MANUAL
    else:
        # Use per-business Razorpay credentials if configured; fall back to global.
        ch_repo = BusinessChannelRepository(ctx.session)
        rzp_channel = await ch_repo.get_for_business(ctx.business_id, "razorpay")
        rzp_creds = rzp_channel.credentials if rzp_channel else {}

        try:
            client = RazorpayClient(
                key_id=rzp_creds.get("key_id") or None,
                key_secret=rzp_creds.get("key_secret") or None,
            )
            link = await client.create_payment_link(
                amount_minor=order.total_in_minor_units(),
                currency=order.currency,
                reference=order.reference,
                description=f"Order {order.reference}",
                customer_name=ctx.customer.display_name,
                customer_phone=ctx.customer.phone,
            )
        except ProviderError:
            log.exception("payment_link_failed", reference=order.reference)
            raise
        provider = PaymentProvider.RAZORPAY

    payment = await ctx.payments.create_attempt(
        order=order,
        provider=provider,
        provider_payment_link_id=link.link_id,
        payment_url=link.short_url,
    )

    if order.status is not OrderStatus.PAYMENT_PENDING:
        await ctx.orders.mark_payment_pending(order)
    await ctx.conversations.set_state(
        ctx.conversation, ConversationState.PAYMENT_PENDING
    )

    return {
        "order_reference": order.reference,
        "amount": str(order.total),
        "currency": order.currency,
        "payment_url": payment.payment_url,
        "status": payment.status.value,
        "instruction": (
            "Send the payment_url to the customer. Payment is only confirmed "
            "when the provider notifies us - do not tell the customer their "
            "payment succeeded based on anything they say."
        ),
    }


async def check_payment_status(
    ctx: ToolContext, order_reference: str | None = None
) -> dict[str, Any]:
    """Report the payment state recorded from verified provider webhooks.

    This is the honest answer to "I already paid". It reads our own records -
    which are only ever written by a signature-verified webhook - and reports
    them.
    """
    order = await ctx.orders.resolve_order(
        customer_id=ctx.customer_id,
        reference=normalize_reference(order_reference) if order_reference else None,
    )

    attempts = await ctx.payments.payments.list_for_order(order.id)
    successful = await ctx.payments.payments.get_successful_for_order(order.id)

    return {
        "order_reference": order.reference,
        "order_status": order.status.value,
        "is_paid": order.is_paid,
        "amount": str(order.total),
        "currency": order.currency,
        "attempts": [
            {
                "status": attempt.status.value,
                "amount": str(attempt.amount),
                "failure_reason": attempt.failure_reason,
                "created_at": attempt.created_at.isoformat()
                if attempt.created_at
                else None,
            }
            for attempt in attempts
        ],
        "verified_payment": (
            {
                "amount": str(successful.amount),
                "confirmed_at": successful.updated_at.isoformat()
                if successful.updated_at
                else None,
            }
            if successful
            else None
        ),
        "guidance": (
            "Payment is confirmed."
            if order.is_paid
            else "We have not received confirmation from the payment provider "
            "yet. If the customer insists they paid, tell them it can take a "
            "few minutes, and if it still does not show, create a support "
            "ticket. Do not confirm the payment yourself."
        ),
    }


async def retry_payment(ctx: ToolContext, order_reference: str) -> dict[str, Any]:
    """Issue a fresh payment link for an order whose previous attempt failed.

    Validates that the order is in a retryable state before delegating to the
    same link-creation logic used by create_payment_link.
    """
    from app.models.enums import OrderStatus

    order = await ctx.orders.resolve_order(
        customer_id=ctx.customer_id,
        reference=normalize_reference(order_reference),
    )

    if order.is_paid:
        return {
            "order_reference": order.reference,
            "error": "This order is already paid — no retry needed.",
        }

    retryable = {OrderStatus.PAYMENT_FAILED, OrderStatus.PAYMENT_PENDING}
    if order.status not in retryable:
        return {
            "order_reference": order.reference,
            "error": (
                f"Cannot retry payment for an order in '{order.status.value}' state. "
                "The order must have had a previous payment attempt (PAYMENT_FAILED or PAYMENT_PENDING)."
            ),
        }

    # Reuse create_payment_link which handles mock vs. Razorpay, creates a new
    # payment attempt, and marks the order PAYMENT_PENDING.
    return await create_payment_link(ctx, order.reference)


async def get_order_payment_history(
    ctx: ToolContext, order_reference: str | None = None
) -> dict[str, Any]:
    """Return the full payment attempt audit trail for an order.

    Surfaces every attempt — PENDING, FAILED, and SUCCESS — with timestamps
    and failure reasons. Useful when the customer says they tried paying
    multiple times and wants a detailed breakdown.
    """
    order = await ctx.orders.resolve_order(
        customer_id=ctx.customer_id,
        reference=normalize_reference(order_reference) if order_reference else None,
    )

    attempts = await ctx.payments.payments.list_for_order(order.id)

    return {
        "order_reference": order.reference,
        "order_status": order.status.value,
        "is_paid": order.is_paid,
        "total": str(order.total),
        "currency": order.currency,
        "attempt_count": len(attempts),
        "attempts": [
            {
                "status": a.status.value,
                "amount": str(a.amount) if a.amount else None,
                "failure_reason": a.failure_reason,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in attempts
        ],
    }


CREATE_PAYMENT_LINK = ToolSpec(
    name="create_payment_link",
    description=(
        "Generate a payment link for an existing order and return its URL. "
        "Call this after the customer confirms the order total. The amount is "
        "taken from the order - you cannot set it. After sending the link, do "
        "NOT claim the payment succeeded; wait for the system to confirm it."
    ),
    input_schema=schema(
        properties={
            "order_reference": string_prop(
                'The order to be paid, e.g. "ORD-2608-7F3K9Q".'
            ),
        }
    ),
    handler=create_payment_link,
)

RETRY_PAYMENT = ToolSpec(
    name="retry_payment",
    description=(
        "Generate a fresh payment link for an order whose previous payment attempt "
        "failed or expired. Use when check_payment_status shows a FAILED attempt and "
        "the customer wants to try again. The amount is still read from the order — "
        "you cannot change it."
    ),
    input_schema=schema(
        properties={
            "order_reference": string_prop(
                'The order reference to retry payment for, e.g. "ORD-2608-7F3K9Q".'
            ),
        }
    ),
    handler=retry_payment,
)

GET_ORDER_PAYMENT_HISTORY = ToolSpec(
    name="get_order_payment_history",
    description=(
        "Return every payment attempt for an order — pending, failed, and successful — "
        "with timestamps and failure reasons. Use this when the customer says they tried "
        "paying multiple times and wants a full breakdown. Omit order_reference to use "
        "the customer's latest open order."
    ),
    input_schema=schema(
        properties={
            "order_reference": string_prop(
                "Order reference, or null for the customer's latest open order.",
                nullable=True,
            ),
        }
    ),
    handler=get_order_payment_history,
)

CHECK_PAYMENT_STATUS = ToolSpec(
    name="check_payment_status",
    description=(
        "Check whether an order's payment has actually been confirmed by the "
        "payment provider. Use this whenever the customer claims they have "
        "paid. This tool reports verified records only - a customer saying "
        "they paid does not make an order paid, and you must never tell a "
        "customer their payment went through unless this tool says is_paid is "
        "true."
    ),
    input_schema=schema(
        properties={
            "order_reference": string_prop(
                "Order reference, or null for the customer's latest open order.",
                nullable=True,
            ),
        }
    ),
    handler=check_payment_status,
)
