"""Re-send delivered game credentials to the customer who owns the order.

This is the one place the agent can surface a login — and it is deliberately
narrow. It reveals credentials ONLY for an order that is:

  * owned by the current customer (``ctx.customer_id`` — the model cannot name
    another customer; ``resolve_order`` is customer-scoped), and
  * already FULFILLED (i.e. the account was already delivered to this same
    customer once).

It never triggers a new fulfillment and never reveals anything for an unpaid or
not-yet-delivered order — so it does not weaken rule 2 (only a verified webhook
marks payment) or rule 3 (the agent cannot declare an order delivered). It is a
re-read of something the customer already received: the "I lost my login" path.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, schema, string_prop
from app.core.ids import normalize_reference
from app.models.enums import FulfillmentStatus

log = structlog.get_logger(__name__)


async def get_my_credentials(
    ctx: ToolContext, order_reference: str | None = None
) -> dict[str, Any]:
    """Return the login details for the customer's paid, delivered order."""
    order = await ctx.orders.resolve_order(
        customer_id=ctx.customer_id,
        reference=normalize_reference(order_reference) if order_reference else None,
    )

    if not order.is_paid:
        return {
            "order_reference": order.reference,
            "available": False,
            "reason": "not_paid",
            "message": "This order is not paid yet, so there is no login to send.",
        }

    fulfillment = await ctx.fulfillment.status_for_order(order.id)
    if fulfillment is None or fulfillment.status is not FulfillmentStatus.DELIVERED:
        return {
            "order_reference": order.reference,
            "available": False,
            "reason": "not_delivered",
            "message": (
                "Payment is received but the login has not been delivered yet "
                "(often means it is out of stock). Do not invent one — tell the "
                "customer it is being prepared and create a support ticket if "
                "they need it urgently."
            ),
        }

    items: list[dict[str, str]] = []
    for entry in fulfillment.metadata_.get("credential_refs", []):
        revealed = await ctx.credentials.reveal(uuid.UUID(entry["credential_id"]))
        if revealed is not None:
            items.append(
                {
                    "product": entry.get("product_name", ""),
                    "username": revealed.username,
                    "password": revealed.password,
                }
            )

    log.info(
        "credentials_resent",
        reference=order.reference,
        customer_id=str(ctx.customer_id),
        count=len(items),
    )
    return {
        "order_reference": order.reference,
        "available": True,
        "credentials": items,
        "instruction": (
            "Send these login details to the customer now, in their language. "
            "These belong to this customer's paid, delivered order."
        ),
    }


GET_MY_CREDENTIALS = ToolSpec(
    name="get_my_credentials",
    description=(
        "Retrieve and re-send the game login (ID + password) for THIS customer's "
        "order, but only if the order is paid and already delivered. Use it when "
        "a paying customer asks for their login again or says they lost it. If it "
        "returns available=false, do NOT make up a login — follow the message it "
        "returns."
    ),
    input_schema=schema(
        properties={
            "order_reference": string_prop(
                "Order reference, or null for the customer's most recent order.",
                nullable=True,
            ),
        }
    ),
    handler=get_my_credentials,
)
