"""Admin-only support tools: ticket management, customer lookup, outbound messaging.

Injected alongside ADMIN_TOOLS from admin_knowledge.py when admin_mode=True.
Requires X-Internal-Key header — never available in customer-facing channels.

Follows the same pattern as admin_knowledge.py: direct repository instantiation,
error dicts not raised exceptions, no tenant parameters in schemas.
"""

from __future__ import annotations

from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, enum_prop, integer_prop, schema, string_prop


async def list_business_tickets(
    ctx: ToolContext,
    priority: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List open support tickets for this business, optionally by priority."""
    from app.models.enums import TicketPriority
    from app.repositories.support_tickets import SupportTicketRepository

    repo = SupportTicketRepository(ctx.session, ctx.business_id)
    cap = min(limit or 20, 50) if limit else 20

    priority_enum: TicketPriority | None = None
    if priority and priority.upper() in ("LOW", "MEDIUM", "HIGH", "URGENT"):
        priority_enum = TicketPriority(priority.upper())

    tickets = await repo.list_open(priority=priority_enum, limit=cap)

    return {
        "count": len(tickets),
        "tickets": [
            {
                "reference": t.reference,
                "reason": t.reason,
                "priority": t.priority.value,
                "status": t.status.value,
                "summary": t.summary,
                "assigned_to": t.assigned_to,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ],
        "note": "Use ticket 'reference' values when calling assign_ticket or resolve_ticket.",
    }


LIST_BUSINESS_TICKETS = ToolSpec(
    name="list_business_tickets",
    description=(
        "List all open support tickets for this business, optionally filtered by "
        "priority. Returns reference, reason, priority, assigned_to, and summary. "
        "Use the reference to call assign_ticket or resolve_ticket."
    ),
    input_schema=schema(
        properties={
            "priority": string_prop(
                "Filter to this priority only: LOW, MEDIUM, HIGH, or URGENT. "
                "Pass null to return all priorities.",
                nullable=True,
            ),
            "limit": integer_prop(
                "Maximum tickets to return (1–50, default 20).",
                minimum=1,
                maximum=50,
                nullable=True,
            ),
        }
    ),
    handler=list_business_tickets,
)


async def assign_ticket(
    ctx: ToolContext, ticket_reference: str, agent_name: str
) -> dict[str, Any]:
    """Assign a support ticket to a human agent."""
    from app.repositories.support_tickets import SupportTicketRepository

    repo = SupportTicketRepository(ctx.session, ctx.business_id)
    ticket = await repo.get_by_reference(ticket_reference)

    if ticket is None:
        return {"error": f"Ticket {ticket_reference!r} not found."}

    assigned = await ctx.support.assign(ticket, agent=agent_name)
    return {
        "reference": assigned.reference,
        "assigned_to": assigned.assigned_to,
        "status": assigned.status.value,
        "message": f"Ticket {assigned.reference} assigned to {agent_name}.",
    }


ASSIGN_TICKET = ToolSpec(
    name="assign_ticket",
    description=(
        "Assign an open support ticket to a named human agent. Moves the ticket to "
        "IN_PROGRESS. Use list_business_tickets to find ticket references."
    ),
    input_schema=schema(
        properties={
            "ticket_reference": string_prop(
                "Ticket reference, e.g. 'TKT-2608-AB12CD'."
            ),
            "agent_name": string_prop(
                "Name or handle of the agent to assign to, e.g. 'priya.k'."
            ),
        }
    ),
    handler=assign_ticket,
)


async def resolve_ticket(
    ctx: ToolContext, ticket_reference: str, resolution: str | None = None
) -> dict[str, Any]:
    """Mark a support ticket as resolved with an optional resolution note."""
    from app.repositories.support_tickets import SupportTicketRepository

    repo = SupportTicketRepository(ctx.session, ctx.business_id)
    ticket = await repo.get_by_reference(ticket_reference)

    if ticket is None:
        return {"error": f"Ticket {ticket_reference!r} not found."}

    resolved = await ctx.support.resolve(ticket, resolution=resolution)
    return {
        "reference": resolved.reference,
        "status": resolved.status.value,
        "message": f"Ticket {resolved.reference} marked as resolved.",
    }


RESOLVE_TICKET = ToolSpec(
    name="resolve_ticket",
    description=(
        "Mark a support ticket as resolved, optionally with a short resolution note. "
        "Use after the issue has been handled and you want to close it out."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticket_reference": {
                "type": "string",
                "description": "Ticket reference, e.g. 'TKT-2608-AB12CD'.",
            },
            "resolution": {
                "type": ["string", "null"],
                "description": "Optional 1-2 sentence note on what was done to resolve it.",
            },
        },
        "required": ["ticket_reference", "resolution"],
        "additionalProperties": False,
    },
    handler=resolve_ticket,
)


async def lookup_customer(
    ctx: ToolContext, phone: str | None = None
) -> dict[str, Any]:
    """Find a customer by phone number and return their profile and channels."""
    from app.repositories.customers import CustomerChannelRepository, CustomerRepository

    if not phone:
        return {"error": "Provide a phone number to look up a customer."}

    repo = CustomerRepository(ctx.session, ctx.business_id)
    customer = await repo.get_by_phone(phone)

    if customer is None:
        return {"error": f"No customer found with phone {phone!r} for this business."}

    channels = await CustomerChannelRepository(ctx.session, ctx.business_id).list_for_customer(customer.id)
    return {
        "customer_id": str(customer.id),
        "name": customer.display_name,
        "phone": customer.phone,
        "channels": [
            {"channel": ch.channel.value, "external_id": ch.external_user_id}
            for ch in channels
        ],
    }


LOOKUP_CUSTOMER = ToolSpec(
    name="lookup_customer",
    description=(
        "Find a customer by phone number and return their profile and linked channels "
        "(WhatsApp, Telegram, Web). Use when a customer contacts support without their "
        "order reference and you need to pull up their account."
    ),
    input_schema=schema(
        properties={
            "phone": string_prop(
                "Customer phone number including country code, e.g. '+919876543210'.",
                nullable=True,
            ),
        }
    ),
    handler=lookup_customer,
)


async def send_proactive_message(ctx: ToolContext, text: str) -> dict[str, Any]:
    """Send an outbound message to the current conversation's customer."""
    from app.services.notify import send_to_customer

    sent = await send_to_customer(ctx.session, ctx.business_id, ctx.customer_id, text)

    if sent:
        return {
            "sent": True,
            "customer_id": str(ctx.customer_id),
            "message": "Message delivered to the customer on their primary channel.",
        }
    return {
        "sent": False,
        "note": (
            "Could not deliver: the customer has no WhatsApp or Telegram channel "
            "configured. Web chat has no outbound path."
        ),
    }


SEND_PROACTIVE_MESSAGE = ToolSpec(
    name="send_proactive_message",
    description=(
        "Send a direct message to the current customer on their primary channel "
        "(WhatsApp or Telegram). Use for proactive notifications such as 'your order "
        "is delayed' or 're-sending your credentials'. Web-only customers cannot "
        "receive outbound messages."
    ),
    input_schema=schema(
        properties={
            "text": string_prop(
                "The message text to send. Keep it concise and conversational."
            ),
        }
    ),
    handler=send_proactive_message,
)


ADMIN_SUPPORT_TOOLS: tuple[ToolSpec, ...] = (
    LIST_BUSINESS_TICKETS,
    ASSIGN_TICKET,
    RESOLVE_TICKET,
    LOOKUP_CUSTOMER,
    SEND_PROACTIVE_MESSAGE,
)
