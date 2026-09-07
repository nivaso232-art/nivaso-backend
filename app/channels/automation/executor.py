"""Connector automation executor.

Drives the button/menu workflow tree stored in ``connector_automations.flow``.
Stateless by design: all conversation state is persisted in
``conversation.metadata_["automation"]``.

Flow JSON structure
-------------------
{
  "welcome_message": "...",
  "fallback_message": "...",
  "nodes": [
    {
      "id": "root",
      "type": "menu",      # menu | message | action
      "message": "How can I help?",
      "options": [
        {
          "id": "opt1",
          "label": "Browse Products",
          "description": "",         # optional, shown in list-type menus
          "next": "node_id",         # navigate to this node
          "action": {                # optional: execute a module function
            "module": "products",
            "function": "list_categories",
            "input_param": ""        # optional: use user's message as param
          }
        }
      ]
    }
  ]
}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.automation.modules import execute_module_function
from app.core.uow import UnitOfWork
from app.repositories.connector_automations import ConnectorAutomationRepository

if TYPE_CHECKING:
    from app.models.business import Business
    from app.models.conversation import Conversation
    from app.models.customer import Customer

log = structlog.get_logger(__name__)


@dataclass
class AutomationResult:
    """Result of one executor turn."""

    text: str
    options: list[dict[str, str]] | None  # [{id, label, description}]
    menu_type: str | None  # "buttons" (<=3 options) or "list" (>3 options)
    handled: bool = True


class AutomationExecutor:
    """Stateless executor for connector automation flows.

    Conversation state (current node) lives in
    ``conversation.metadata_["automation"]["node"]``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def handle(
        self,
        *,
        session: AsyncSession,
        business: "Business",
        customer: "Customer",
        conversation: "Conversation",
        connector_type: str,
        user_message: str,
    ) -> AutomationResult | None:
        """Process one inbound message and return the automation response.

        Returns None when no active automation flow exists for this connector.
        Returns an AutomationResult with handled=True when the flow took over.
        """
        repo = ConnectorAutomationRepository(session)
        automation = await repo.get_active_flow(business.id, connector_type)
        if automation is None:
            return None

        flow: dict[str, Any] = automation.flow or {}
        nodes: list[dict[str, Any]] = flow.get("nodes", [])
        welcome_message: str = flow.get("welcome_message", "")
        fallback_message: str = flow.get(
            "fallback_message",
            "Sorry, I didn't understand. Please choose an option.",
        )

        if not nodes:
            # Flow is empty — nothing to do; fall through to AI
            return None

        # Build a node lookup
        node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}
        root_node = node_map.get("root") or nodes[0]

        # Read current node from conversation metadata
        auto_meta: dict[str, Any] = (conversation.metadata_ or {}).get(
            "automation", {}
        )
        current_node_id: str = auto_meta.get("node", "root")
        awaiting_input: bool = auto_meta.get("awaiting_input", False)
        pending_action: dict[str, Any] | None = auto_meta.get("pending_action")

        # -- If the previous turn asked for user text input, handle it now ---
        if awaiting_input and pending_action:
            action_text = await execute_module_function(
                pending_action["module"],
                pending_action["function"],
                session=session,
                business=business,
                customer=customer,
                user_input=user_message,
            )
            # After executing, go to the "next" node if specified
            next_node_id = pending_action.get("next", "root")
            next_node = node_map.get(next_node_id, root_node)
            await self._save_node(
                session, conversation, next_node_id,
                awaiting_input=False, pending_action=None,
            )
            menu_text, options = self._render_node(next_node)
            full_text = action_text + "\n\n" + menu_text if menu_text else action_text
            return AutomationResult(
                text=full_text,
                options=options or None,
                menu_type=_menu_type(options),
            )

        # -- First message or explicit "root" request ------------------------
        current_node = node_map.get(current_node_id, root_node)
        is_first_message = current_node_id == "root" and not auto_meta

        if is_first_message:
            # Show welcome + root menu
            menu_text, options = self._render_node(root_node)
            full_text = (
                f"{welcome_message}\n\n{menu_text}" if welcome_message else menu_text
            )
            await self._save_node(session, conversation, root_node["id"])
            return AutomationResult(
                text=full_text,
                options=options or None,
                menu_type=_menu_type(options),
            )

        # -- Match user input to an option -----------------------------------
        options_list: list[dict[str, Any]] = current_node.get("options", [])
        matched_option = _match_option(user_message, options_list)

        if matched_option is None:
            # No match — repeat current menu with fallback message
            menu_text, options = self._render_node(current_node)
            full_text = f"{fallback_message}\n\n{menu_text}"
            return AutomationResult(
                text=full_text,
                options=options or None,
                menu_type=_menu_type(options),
            )

        # -- Handle the matched option ---------------------------------------
        action = matched_option.get("action")
        next_node_id = matched_option.get("next", "root")

        action_text = ""
        if action:
            module = action.get("module", "")
            function = action.get("function", "")
            input_param = action.get("input_param", "")

            if input_param:
                # The action needs user input — store it and ask
                await self._save_node(
                    session,
                    conversation,
                    current_node_id,
                    awaiting_input=True,
                    pending_action={
                        "module": module,
                        "function": function,
                        "next": next_node_id,
                    },
                )
                return AutomationResult(
                    text=f"Please enter your {input_param}:",
                    options=None,
                    menu_type=None,
                )
            else:
                # Execute immediately
                action_text = await execute_module_function(
                    module,
                    function,
                    session=session,
                    business=business,
                    customer=customer,
                    user_input=user_message,
                )

        # Navigate to the next node
        next_node = node_map.get(next_node_id, root_node)
        await self._save_node(session, conversation, next_node_id)

        menu_text, next_options = self._render_node(next_node)
        if action_text and menu_text:
            full_text = f"{action_text}\n\n{menu_text}"
        elif action_text:
            full_text = action_text
        else:
            full_text = menu_text

        return AutomationResult(
            text=full_text,
            options=next_options or None,
            menu_type=_menu_type(next_options),
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _render_node(
        self, node: dict[str, Any]
    ) -> tuple[str, list[dict[str, str]]]:
        """Return (message_text, options_list) for the given node."""
        message = node.get("message", "")
        options = node.get("options", [])
        rendered_options = [
            {
                "id": opt.get("id", str(i)),
                "label": opt.get("label", f"Option {i + 1}"),
                "description": opt.get("description", ""),
            }
            for i, opt in enumerate(options)
        ]
        return message, rendered_options

    async def _save_node(
        self,
        session: AsyncSession,
        conversation: "Conversation",
        node_id: str,
        *,
        awaiting_input: bool = False,
        pending_action: dict[str, Any] | None = None,
    ) -> None:
        """Persist the current automation node into conversation.metadata_."""
        meta = dict(conversation.metadata_ or {})
        meta["automation"] = {
            "node": node_id,
            "awaiting_input": awaiting_input,
            "pending_action": pending_action,
        }
        async with UnitOfWork(session):
            conversation.metadata_ = meta
            await session.flush()


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def _match_option(
    user_message: str, options: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Match user input to one of the menu options.

    Accepts:
      - A position number ("1", "2", "3" …)
      - A case-insensitive label match (substring or exact)
      - The option's id
    """
    text = user_message.strip()
    if not text or not options:
        return None

    # Positional match ("1" → options[0])
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(options):
            return options[idx]
        return None

    text_lower = text.lower()

    # Exact id match
    for opt in options:
        if opt.get("id", "").lower() == text_lower:
            return opt

    # Exact label match (case-insensitive)
    for opt in options:
        if opt.get("label", "").lower() == text_lower:
            return opt

    # Substring label match
    for opt in options:
        if text_lower in opt.get("label", "").lower():
            return opt

    return None


def _menu_type(options: list[dict[str, Any]] | None) -> str | None:
    """Determine whether to use buttons (<=3) or list (>3) presentation."""
    if not options:
        return None
    return "buttons" if len(options) <= 3 else "list"
