"""Tool registry.

Order is fixed and deliberate, never computed. The rendered tool list is part of
the request's cached prefix (render order: tools -> system -> messages), so a
set-iteration or dict-ordering change here invalidates every tenant's prompt
cache on deploy and quietly multiplies input cost.

Group order: discovery → orders → payments → knowledge → support → context → delivery.
New tools are always appended within the correct group, never inserted mid-group.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.base import ToolSpec
from app.agent.tools.catalog import (
    CHECK_PRODUCT_AVAILABILITY,
    COMPARE_PRODUCTS,
    GET_PRODUCT,
    SEARCH_PRODUCTS,
)
from app.agent.tools.context_tools import GET_CONVERSATION_SUMMARY
from app.agent.tools.delivery import GET_FULFILLMENT_DETAILS, GET_MY_CREDENTIALS
from app.agent.tools.knowledge import GET_FULL_ARTICLE, SEARCH_KNOWLEDGE
from app.agent.tools.orders import (
    CANCEL_ORDER,
    CREATE_ORDER,
    GET_ORDER_STATUS,
    LIST_MY_ORDERS,
)
from app.agent.tools.payments import (
    CHECK_PAYMENT_STATUS,
    CREATE_PAYMENT_LINK,
    GET_ORDER_PAYMENT_HISTORY,
    RETRY_PAYMENT,
)
from app.agent.tools.support import (
    CREATE_SUPPORT_TICKET,
    LIST_OPEN_TICKETS,
    UPDATE_SUPPORT_TICKET,
)

TOOLS: tuple[ToolSpec, ...] = (
    # ── discovery ────────────────────────────────────────────────────────────
    SEARCH_PRODUCTS,
    GET_PRODUCT,
    COMPARE_PRODUCTS,
    CHECK_PRODUCT_AVAILABILITY,
    # ── orders ───────────────────────────────────────────────────────────────
    CREATE_ORDER,
    LIST_MY_ORDERS,
    GET_ORDER_STATUS,
    CANCEL_ORDER,
    GET_FULFILLMENT_DETAILS,
    # ── payments ─────────────────────────────────────────────────────────────
    CREATE_PAYMENT_LINK,
    CHECK_PAYMENT_STATUS,
    GET_ORDER_PAYMENT_HISTORY,
    RETRY_PAYMENT,
    # ── knowledge ────────────────────────────────────────────────────────────
    SEARCH_KNOWLEDGE,
    GET_FULL_ARTICLE,
    # ── support ──────────────────────────────────────────────────────────────
    CREATE_SUPPORT_TICKET,
    LIST_OPEN_TICKETS,
    UPDATE_SUPPORT_TICKET,
    # ── context ──────────────────────────────────────────────────────────────
    GET_CONVERSATION_SUMMARY,
    # ── delivery ─────────────────────────────────────────────────────────────
    GET_MY_CREDENTIALS,
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}


def api_tools() -> list[dict[str, Any]]:
    """The ``tools`` parameter for the Messages API."""
    return [tool.to_api_tool() for tool in TOOLS]


def get_tool(name: str) -> ToolSpec | None:
    return TOOLS_BY_NAME.get(name)


def assert_no_tenant_parameters() -> None:
    """Guard rule 4 at import/test time.

    No tool schema may accept a tenant, customer, or conversation identifier —
    those come from ``ToolContext``. This is cheap to check and easy to
    violate by accident when adding a tool, so it is asserted rather than
    trusted to review.
    """
    forbidden = {"business_id", "customer_id", "conversation_id", "tenant_id"}
    for tool in TOOLS:
        properties = set(tool.input_schema.get("properties", {}))
        leaked = properties & forbidden
        if leaked:
            raise AssertionError(
                f"Tool '{tool.name}' exposes tenant-scoped parameters "
                f"{sorted(leaked)}. These must come from ToolContext, not "
                f"from model input."
            )


assert_no_tenant_parameters()
