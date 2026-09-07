"""Automation module catalog and execution.

``AUTOMATION_MODULE_CATALOG`` is the frontend-consumable registry of every
module + function that can be wired into an automation node action. The
frontend reads GET /admin/{slug}/automations/modules and renders a picker.

``execute_module_function`` is the runtime side: given a module/function name
and optional user input it performs the real DB work and returns a
human-readable text string suitable for sending back over WhatsApp or Telegram.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models.business import Business
    from app.models.customer import Customer

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module catalog — consumed by the frontend flow builder
# ---------------------------------------------------------------------------

AUTOMATION_MODULE_CATALOG: dict[str, dict[str, Any]] = {
    "products": {
        "label": "Products",
        "functions": {
            "list_categories": {
                "label": "List All Categories",
                "returns": "list",
            },
            "search_products": {
                "label": "Search Products",
                "input_param": "query",
                "returns": "list",
            },
        },
    },
    "orders": {
        "label": "Orders",
        "functions": {
            "list_my_orders": {
                "label": "My Recent Orders",
                "returns": "list",
            },
            "get_order_status": {
                "label": "Order Status",
                "input_param": "order_ref",
                "returns": "item",
            },
        },
    },
    "support": {
        "label": "Support",
        "functions": {
            "create_support_ticket": {
                "label": "Raise Support Ticket",
                "input_param": "description",
                "returns": "item",
            },
            "list_open_tickets": {
                "label": "My Open Tickets",
                "returns": "list",
            },
        },
    },
    "knowledge": {
        "label": "Knowledge Base",
        "functions": {
            "search_knowledge": {
                "label": "Search Help Articles",
                "input_param": "query",
                "returns": "list",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Execution entry point
# ---------------------------------------------------------------------------


async def execute_module_function(
    module: str,
    function: str,
    *,
    session: AsyncSession,
    business: "Business",
    customer: "Customer",
    user_input: str = "",
) -> str:
    """Execute a module function and return a formatted text response.

    Each branch performs the real DB call and formats the result as a
    human-readable string for delivery over WhatsApp/Telegram.

    Unknown module/function combinations return a generic error string so the
    caller can safely pass it through without crashing.
    """
    try:
        if module == "products":
            return await _exec_products(
                function, session=session, business=business, user_input=user_input
            )
        if module == "orders":
            return await _exec_orders(
                function,
                session=session,
                business=business,
                customer=customer,
                user_input=user_input,
            )
        if module == "support":
            return await _exec_support(
                function,
                session=session,
                business=business,
                customer=customer,
                user_input=user_input,
            )
        if module == "knowledge":
            return await _exec_knowledge(
                function, session=session, business=business, user_input=user_input
            )
    except Exception as exc:
        log.exception(
            "automation_module_execution_failed",
            module=module,
            function=function,
            error=str(exc),
        )
        return "Sorry, I couldn't fetch that information right now. Please try again."

    return f"Unknown module '{module}' or function '{function}'."


# ---------------------------------------------------------------------------
# Products module
# ---------------------------------------------------------------------------


async def _exec_products(
    function: str,
    *,
    session: AsyncSession,
    business: "Business",
    user_input: str,
) -> str:
    from app.services.catalog_service import CatalogService

    svc = CatalogService(session, business.id)

    if function == "list_categories":
        categories = await svc.list_categories()
        if not categories:
            return "No product categories are available at the moment."
        lines = "\n".join(f"• {c}" for c in categories)
        return f"Our product categories:\n{lines}"

    if function == "search_products":
        query = user_input.strip() or "all"
        results = await svc.search_products(query, limit=5)
        if not results:
            return f"No products found for '{query}'."
        lines = "\n".join(f"• {p.name} — ₹{p.price}" for p in results)
        return f"Products matching '{query}':\n{lines}"

    return f"Unknown products function: {function}"


# ---------------------------------------------------------------------------
# Orders module
# ---------------------------------------------------------------------------


async def _exec_orders(
    function: str,
    *,
    session: AsyncSession,
    business: "Business",
    customer: "Customer",
    user_input: str,
) -> str:
    from app.repositories.orders import OrderRepository

    repo = OrderRepository(session, business.id)

    if function == "list_my_orders":
        orders = await repo.list_for_customer(customer.id, limit=5)
        if not orders:
            return "You don't have any orders yet."
        lines = []
        for order in orders:
            item_names = ", ".join(
                getattr(item, "product_name", "item") for item in (order.items or [])
            )
            lines.append(
                f"• #{order.reference} — {order.status.value}"
                + (f" — {item_names}" if item_names else "")
            )
        return "Your recent orders:\n" + "\n".join(lines)

    if function == "get_order_status":
        ref = user_input.strip()
        if not ref:
            return "Please provide your order reference number."
        order = await repo.get_by_reference(ref)
        if order is None:
            return f"No order found with reference '{ref}'."
        return (
            f"Order #{order.reference}\n"
            f"Status: {order.status.value}\n"
            f"Total: ₹{order.total}"
        )

    return f"Unknown orders function: {function}"


# ---------------------------------------------------------------------------
# Support module
# ---------------------------------------------------------------------------


async def _exec_support(
    function: str,
    *,
    session: AsyncSession,
    business: "Business",
    customer: "Customer",
    user_input: str,
) -> str:
    from app.repositories.support_tickets import SupportTicketRepository
    from app.models.enums import TicketPriority, TicketStatus
    from app.models.support_ticket import SupportTicket
    from app.core.uow import UnitOfWork
    import time

    repo = SupportTicketRepository(session, business.id)

    if function == "list_open_tickets":
        tickets = await repo.list_for_customer(customer.id, limit=5)
        open_tickets = [
            t
            for t in tickets
            if t.status not in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        ]
        if not open_tickets:
            return "You have no open support tickets."
        lines = [
            f"• #{t.reference} — {t.priority.value} — {t.reason or 'General'}"
            for t in open_tickets
        ]
        return "Your open tickets:\n" + "\n".join(lines)

    if function == "create_support_ticket":
        description = user_input.strip()
        if not description:
            return "Please describe your issue and send it again."
        # Generate a short customer-facing reference
        ref = f"TKT-{int(time.time()) % 1_000_000:06d}"
        ticket = SupportTicket(
            business_id=business.id,
            customer_id=customer.id,
            reference=ref,
            reason="CUSTOMER_REQUEST",
            summary=description[:500],
            priority=TicketPriority.MEDIUM,
            status=TicketStatus.OPEN,
        )
        async with UnitOfWork(session):
            session.add(ticket)
            await session.flush()
        return (
            f"Support ticket created!\n"
            f"Reference: #{ref}\n"
            f"Our team will get back to you shortly."
        )

    return f"Unknown support function: {function}"


# ---------------------------------------------------------------------------
# Knowledge module
# ---------------------------------------------------------------------------


async def _exec_knowledge(
    function: str,
    *,
    session: AsyncSession,
    business: "Business",
    user_input: str,
) -> str:
    from app.services.knowledge_service import KnowledgeService

    svc = KnowledgeService(session, business.id)

    if function == "search_knowledge":
        query = user_input.strip()
        if not query:
            return "Please tell me what you're looking for."
        hits = await svc.search(query, limit=3)
        if not hits:
            return f"No help articles found for '{query}'."
        lines = [f"• {hit.title}" for hit in hits]
        return f"Help articles for '{query}':\n" + "\n".join(lines)

    return f"Unknown knowledge function: {function}"
