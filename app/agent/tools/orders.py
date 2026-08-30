"""Order tools: ``create_order``, ``get_order_status``, ``cancel_order``.

Note the shape of ``create_order``'s schema: items are ``{product_id,
quantity}`` and nothing else. There is no price, no total, no discount, no
currency. That absence *is* rule 1 - the model cannot state a price to the
order layer because the schema gives it nowhere to put one.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, schema, string_prop
from app.core.errors import ValidationError
from app.core.ids import normalize_reference
from app.models.enums import ConversationState
from app.models.order import Order
from app.services.order_service import OrderLineRequest


def _serialize(order: Order, *, fulfillment_status: str | None = None) -> dict[str, Any]:
    return {
        "order_reference": order.reference,
        "status": order.status.value,
        "currency": order.currency,
        "subtotal": str(order.subtotal),
        "discount": str(order.discount),
        "total": str(order.total),
        "items": [
            {
                "name": item.product_name,
                "unit_price": str(item.unit_price),
                "quantity": item.quantity,
                "line_total": str(item.total),
            }
            for item in order.items
        ],
        "fulfillment_status": fulfillment_status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


async def create_order(
    ctx: ToolContext, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Create an order for the current customer.

    Prices come from the database, not from ``items``.
    """
    if not items:
        raise ValidationError("At least one item is required.")

    lines: list[OrderLineRequest] = []
    for entry in items:
        raw_id = entry.get("product_id")
        if not raw_id:
            raise ValidationError("Every item needs a product_id.")
        try:
            product_id = uuid.UUID(str(raw_id))
        except ValueError as exc:
            raise ValidationError(
                "product_id must be a UUID from search_products.",
                details={"product_id": raw_id},
            ) from exc

        quantity = entry.get("quantity", 1)
        if not isinstance(quantity, int):
            raise ValidationError(
                "quantity must be a whole number.",
                details={"quantity": quantity},
            )
        lines.append(OrderLineRequest(product_id=product_id, quantity=quantity))

    order = await ctx.orders.create_order(
        customer_id=ctx.customer_id,
        lines=lines,
        conversation_id=ctx.conversation_id,
    )
    await ctx.conversations.set_state(
        ctx.conversation, ConversationState.WAITING_CONFIRMATION
    )

    return {
        **_serialize(order),
        "next_step": (
            "Confirm the total with the customer, then call "
            "create_payment_link to send them a payment link."
        ),
    }


async def get_order_status(
    ctx: ToolContext, order_reference: str | None = None
) -> dict[str, Any]:
    """Look up an order.

    With no reference, resolves to the customer's most recent open order -
    which is what "where's my game?" almost always means (edge case 20).
    """
    reference = normalize_reference(order_reference) if order_reference else None
    order = await ctx.orders.resolve_order(
        customer_id=ctx.customer_id, reference=reference
    )

    fulfillment = await ctx.fulfillment.status_for_order(order.id)
    return _serialize(
        order,
        fulfillment_status=fulfillment.status.value if fulfillment else None,
    )


async def cancel_order(
    ctx: ToolContext, order_reference: str, reason: str
) -> dict[str, Any]:
    """Cancel an unpaid order (edge case 17).

    Refuses on a paid order - that needs a refund, which is a human decision.
    """
    order = await ctx.orders.resolve_order(
        customer_id=ctx.customer_id,
        reference=normalize_reference(order_reference),
    )
    cancelled = await ctx.orders.cancel_order(order, reason=reason)
    return {
        **_serialize(cancelled),
        "message": f"Order {cancelled.reference} has been cancelled.",
    }


CREATE_ORDER = ToolSpec(
    name="create_order",
    description=(
        "Create an order once the customer has clearly confirmed they want to "
        "buy. Pass only product_id and quantity - pricing is calculated by the "
        "system from the catalog, and any price you state is not used. Do not "
        "call this while the customer is still browsing or asking questions."
    ),
    input_schema=schema(
        properties={
            "items": {
                "type": "array",
                "description": "The products the customer is buying.",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "product_id": {
                            "type": "string",
                            "description": "product_id from search_products.",
                        },
                        "quantity": {
                            "type": "integer",
                            "description": "How many. Defaults to 1.",
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "required": ["product_id", "quantity"],
                    "additionalProperties": False,
                },
            }
        }
    ),
    handler=create_order,
)

GET_ORDER_STATUS = ToolSpec(
    name="get_order_status",
    description=(
        "Check the status of the customer's order, including whether it is "
        "paid and whether it has been delivered. Omit order_reference to get "
        "their most recent open order - use that when the customer says "
        'something like "where is my order?" without quoting a reference. '
        "Always report the status this tool returns; never guess."
    ),
    input_schema=schema(
        properties={
            "order_reference": string_prop(
                'Order reference such as "ORD-2608-7F3K9Q". '
                "Pass null to use the customer's latest open order.",
                nullable=True,
            ),
        }
    ),
    handler=get_order_status,
)

CANCEL_ORDER = ToolSpec(
    name="cancel_order",
    description=(
        "Cancel an order the customer has NOT yet paid for. If the order is "
        "already paid this will fail - in that case tell the customer a team "
        "member will handle the refund and call create_support_ticket. Confirm "
        "with the customer before calling this."
    ),
    input_schema=schema(
        properties={
            "order_reference": string_prop(
                'The order reference to cancel, e.g. "ORD-2608-7F3K9Q".'
            ),
            "reason": string_prop(
                "Short reason in the customer's own words, "
                'e.g. "changed mind, wants RDR 2 instead".'
            ),
        }
    ),
    handler=cancel_order,
)
