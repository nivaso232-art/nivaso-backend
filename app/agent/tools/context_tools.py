"""Context tool: ``get_conversation_summary``.

Surfaces a structured snapshot of the current conversation — state, customer
name, any open order, any open ticket — so the agent can hand off rich context
to a human or summarise the conversation on request.
"""

from __future__ import annotations

from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, schema


async def get_conversation_summary(ctx: ToolContext) -> dict[str, Any]:
    """Return a structured summary of the current conversation."""
    from app.repositories.support_tickets import SupportTicketRepository

    # Open support ticket for this conversation, if any
    repo = SupportTicketRepository(ctx.session, ctx.business_id)
    open_ticket = await repo.get_open_for_conversation(ctx.conversation_id)

    # Latest open order for this customer, if any
    try:
        order = await ctx.orders.resolve_order(
            customer_id=ctx.customer_id, reference=None
        )
        order_info: dict[str, Any] | None = {
            "reference": order.reference,
            "status": order.status.value,
            "total": str(order.total),
            "currency": order.currency,
        }
    except Exception:
        order_info = None

    return {
        "conversation_state": ctx.conversation.state.value,
        "customer_name": ctx.customer.display_name,
        "open_order": order_info,
        "open_ticket": (
            {
                "reference": open_ticket.reference,
                "reason": open_ticket.reason,
                "status": open_ticket.status.value,
                "priority": open_ticket.priority.value,
            }
            if open_ticket
            else None
        ),
    }


GET_CONVERSATION_SUMMARY = ToolSpec(
    name="get_conversation_summary",
    description=(
        "Return a structured snapshot of the current conversation: the conversation "
        "state, customer name, any open order, and any open support ticket. Use this "
        "when asked to summarise the conversation, prepare a handoff note for a human "
        "agent, or check what is outstanding before closing."
    ),
    input_schema=schema(properties={}),
    handler=get_conversation_summary,
)
