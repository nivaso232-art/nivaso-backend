"""Support tool: ``create_support_ticket``.

The escape hatch. An agent that cannot escalate will instead improvise, and an
improvising agent on a payment problem is worse than a two-minute wait for a
human.
"""

from __future__ import annotations

from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, enum_prop, integer_prop, schema, string_prop
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


async def list_open_tickets(
    ctx: ToolContext, limit: int | None = None
) -> dict[str, Any]:
    """Return the current customer's support tickets, newest first."""
    from app.repositories.support_tickets import SupportTicketRepository

    cap = min(limit or 5, 10) if limit else 5
    repo = SupportTicketRepository(ctx.session, ctx.business_id)
    tickets = await repo.list_for_customer(ctx.customer_id, limit=cap)

    if not tickets:
        return {"count": 0, "tickets": [], "note": "No support tickets found for your account."}

    return {
        "count": len(tickets),
        "tickets": [
            {
                "reference": t.reference,
                "reason": t.reason,
                "status": t.status.value,
                "priority": t.priority.value,
                "summary": t.summary,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ],
    }


LIST_OPEN_TICKETS = ToolSpec(
    name="list_open_tickets",
    description=(
        "Return the customer's support tickets (open and recently resolved), newest "
        "first. Use when the customer asks 'what's the status of my complaint?' or "
        "'did my ticket get resolved?' without quoting a specific reference."
    ),
    input_schema=schema(
        properties={
            "limit": integer_prop(
                "Maximum tickets to return (1–10). Defaults to 5.",
                minimum=1,
                maximum=10,
                nullable=True,
            ),
        }
    ),
    handler=list_open_tickets,
)


async def update_support_ticket(
    ctx: ToolContext, ticket_reference: str, additional_info: str
) -> dict[str, Any]:
    """Append additional information to an existing open support ticket."""
    from app.repositories.support_tickets import SupportTicketRepository

    repo = SupportTicketRepository(ctx.session, ctx.business_id)
    ticket = await repo.get_by_reference(ticket_reference)

    if ticket is None or ticket.customer_id != ctx.customer_id:
        return {"error": f"Ticket {ticket_reference!r} not found on your account."}

    if not ticket.is_open:
        return {
            "error": f"Ticket {ticket_reference!r} is already resolved and cannot be updated.",
            "status": ticket.status.value,
        }

    ticket.summary = (
        ((ticket.summary or "") + f"\n\nCustomer update: {additional_info}").strip()
    )
    await ctx.session.flush()

    return {
        "ticket_reference": ticket.reference,
        "status": ticket.status.value,
        "message": (
            f"Your update has been added to ticket {ticket.reference}. "
            "A team member will review it."
        ),
    }


UPDATE_SUPPORT_TICKET = ToolSpec(
    name="update_support_ticket",
    description=(
        "Add more information to an existing open support ticket. Use when a customer "
        "wants to provide an update or forgotten detail after a ticket was already "
        "created — for example, their order reference or a screenshot description. "
        "The ticket_reference comes from create_support_ticket or list_open_tickets."
    ),
    input_schema=schema(
        properties={
            "ticket_reference": string_prop(
                "The ticket reference, e.g. 'TKT-2608-AB12CD'."
            ),
            "additional_info": string_prop(
                "The additional information to append. Write in English."
            ),
        }
    ),
    handler=update_support_ticket,
)


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
