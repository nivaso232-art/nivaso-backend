"""Tool registry.

Order is fixed and alphabetical-by-group, never computed. The rendered tool
list is part of the request's cached prefix (render order is
``tools`` -> ``system`` -> ``messages``), so a set-iteration or dict-ordering
change here would invalidate every tenant's prompt cache on deploy and quietly
multiply input cost.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.base import ToolSpec
from app.agent.tools.catalog import GET_PRODUCT, SEARCH_PRODUCTS
from app.agent.tools.delivery import GET_MY_CREDENTIALS
from app.agent.tools.knowledge import SEARCH_KNOWLEDGE
from app.agent.tools.orders import CANCEL_ORDER, CREATE_ORDER, GET_ORDER_STATUS
from app.agent.tools.payments import CHECK_PAYMENT_STATUS, CREATE_PAYMENT_LINK
from app.agent.tools.support import CREATE_SUPPORT_TICKET

# Deliberate, stable ordering: discovery -> purchase -> payment -> support,
# which is also the order a conversation tends to move through.
TOOLS: tuple[ToolSpec, ...] = (
    SEARCH_PRODUCTS,
    GET_PRODUCT,
    CREATE_ORDER,
    GET_ORDER_STATUS,
    CANCEL_ORDER,
    CREATE_PAYMENT_LINK,
    CHECK_PAYMENT_STATUS,
    GET_MY_CREDENTIALS,
    SEARCH_KNOWLEDGE,
    CREATE_SUPPORT_TICKET,
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}


def api_tools() -> list[dict[str, Any]]:
    """The ``tools`` parameter for the Messages API."""
    return [tool.to_api_tool() for tool in TOOLS]


def get_tool(name: str) -> ToolSpec | None:
    return TOOLS_BY_NAME.get(name)


def assert_no_tenant_parameters() -> None:
    """Guard rule 4 at import/test time.

    No tool schema may accept a tenant, customer, or conversation identifier -
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
