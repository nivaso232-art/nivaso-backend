"""Support tool: ``create_support_ticket``.

The escape hatch. An agent that cannot escalate will instead improvise, and an
improvising agent on a payment problem is worse than a two-minute wait for a
human.
"""

from __future__ import annotations

from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, enum_prop, schema, string_prop
from app.core.ids import normalize_reference
from app.models.enums import ConversationState, TicketPriority

# Kept in sync with support_service.KNOWN_REASONS, minus the ones only the
# system raises (DOUBLE_PAYMENT, PAYMENT_AMOUNT_MISMATCH) - the agent has no
# way to verify those and should not be guessing at them.
AGENT_REASONS = [
    "PRODUCT_ACCESS_PROBLEM",
    "PAYMENT_PROBLEM",
    "REFUND_REQUEST",
    "DELIVERY_DELAY",
    "AI_COULD_NOT_RESOLVE",
    "CUSTOMER_REQUESTED_HUMAN",
    "OTHER",
]


async def create_support_ticket(
    ctx: ToolContext,
    reason: str,
    summary: str,
    priority: str | None = None,
    order_reference: str | None = None,
) -> dict[str, Any]:
    order_id = None
    if order_reference:
        order = await ctx.orders.resolve_order(
            customer_id=ctx.customer_id,
            reference=normalize_reference(order_reference),
        )
        order_id = order.id

    resolved_priority = (
        TicketPriority(priority) if priority else TicketPriority.MEDIUM
    )

    ticket = await ctx.support.create_ticket(
        customer_id=ctx.customer_id,
        reason=reason,
        summary=summary,
        priority=resolved_priority,
        conversation_id=ctx.conversation_id,
        order_id=order_id,
    )
    await ctx.conversations.set_state(
        ctx.conversation, ConversationState.HUMAN_HANDOFF
    )
    ctx.note(f"support_ticket:{ticket.reference}")

    return {
        "ticket_reference": ticket.reference,
        "priority": ticket.priority.value,
        "status": ticket.status.value,
        "instruction": (
            "Tell the customer a team member will follow up, and give them "
            f"the reference {ticket.reference}. Do not promise a specific "
            "resolution time."
        ),
    }


CREATE_SUPPORT_TICKET = ToolSpec(
    name="create_support_ticket",
    description=(
        "Escalate to a human team member. Call this when: you have tried "
        "search_knowledge and the customer's problem is still unresolved; the "
        "customer explicitly asks for a human; the customer wants a refund; or "
        "the customer reports a payment problem you cannot verify. Prefer "
        "escalating over guessing - never invent a fix or a policy. If a "
        "ticket is already open for this conversation, this adds to it rather "
        "than creating a duplicate."
    ),
    input_schema=schema(
        properties={
            "reason": enum_prop(
                "Category that best fits the problem.", AGENT_REASONS
            ),
            "summary": string_prop(
                "2-3 sentences for the human agent: what the customer wants, "
                "what you already tried, and what is still blocked. Write in "
                "English even if the conversation was not."
            ),
            "priority": {
                "type": ["string", "null"],
                "enum": ["LOW", "MEDIUM", "HIGH", "URGENT", None],
                "description": (
                    "Urgency. Use HIGH for payment or access problems on a "
                    "paid order, MEDIUM for most things. Defaults to MEDIUM."
                ),
            },
            "order_reference": string_prop(
                "Related order reference, if the problem concerns an order.",
                nullable=True,
            ),
        }
    ),
    handler=create_support_ticket,
)
