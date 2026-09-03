"""Stateless agent runner for super-admin chat.

Unlike AgentRunner this runner:
  • Takes a SuperAdminContext instead of ToolContext (no business/customer/conversation).
  • Does not persist conversation rows — each request is self-contained.
  • Accepts the conversation history from the caller (client-managed state).
  • Drives the SUPER_ADMIN_TOOLS tool loop and returns the final text reply.

The Anthropic client (_client) is reused from runner.py so SSL configuration
and workspace headers are applied consistently across both runners.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from app.agent.context import SuperAdminContext
from app.agent.tools.super_admin import SUPER_ADMIN_TOOLS
from app.core.config import settings
from app.core.errors import AgentError

log = structlog.get_logger(__name__)

_MAX_ITERATIONS = 10

_SYSTEM = """You are the Nivaso platform AI — an internal tool that helps the super admin manage the platform.

Capabilities (via tools):
• get_platform_overview — high-level stats snapshot
• list_businesses — all businesses, filterable by plan/status
• get_business — full detail on one business
• create_business — create a new tenant (returns admin credentials)
• change_business_plan — assign a plan tier
• change_business_status — suspend or reactivate
• set_feature_override — enable/disable one flag for a business (additive)
• list_feature_requests — pending or reviewed feature requests
• review_feature_request — approve or deny a pending request
• get_audit_log — recent entitlement audit trail
• update_plan_definition — update defaults for an entire plan tier

Conventions:
• Start with get_platform_overview when the admin asks for a summary without being specific.
• list_businesses returns ALL businesses in one call — including a by_plan grouping. Call it exactly once. There is no plan filter parameter.
• Before suspending a business or downgrading a plan, confirm what you are about to do in one sentence, then proceed.
• When create_business returns admin_password, ALWAYS include it verbatim in your reply — the admin must save it.
• Use business slugs as identifiers, not UUIDs.
• Be concise and operator-focused. No marketing language.""".strip()


class SuperAdminAgentRunner:
    """Drives a stateless super-admin agent turn: call the LLM, execute tools, return reply."""

    def __init__(
        self,
        ctx: SuperAdminContext,
        model: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.model = model or settings.agent_model
        self._tools_by_name = {t.name: t for t in SUPER_ADMIN_TOOLS}
        self._api_tools = [t.to_api_tool() for t in SUPER_ADMIN_TOOLS]

    async def _run_tool(self, spec: Any, arguments: dict[str, Any]) -> Any:
        """Execute one tool in a fresh isolated session.

        Each tool gets its own session so a failed query (e.g. a missing table
        during migrations) cannot put the shared session into PendingRollback
        and silently break every subsequent tool call in the same turn.
        This mirrors the ``_safe_get_ent`` / ``_resolved_flags`` pattern used
        throughout the rest of the codebase.
        """
        from app.core.db import SessionFactory

        try:
            async with SessionFactory() as iso:
                fresh_ctx = SuperAdminContext(
                    session=iso,
                    performed_by=self.ctx.performed_by,
                )
                result = await spec.execute(fresh_ctx, arguments)  # type: ignore[arg-type]
                await iso.commit()
                return result
        except Exception as exc:
            log.warning("super_admin_tool_error", tool=spec.name, error=str(exc))
            return {"error": str(exc)}

    async def run(
        self,
        *,
        history: list[dict[str, Any]],
        user_text: str,
    ) -> tuple[str, list[str]]:
        """Execute a super-admin agent turn.

        Args:
            history: Previous turns as [{role, content}] (client-managed).
            user_text: The admin's current message.

        Returns:
            (reply, tools_used) — the final text reply and list of tool names called.
        """
        # Import here to avoid a circular import (runner.py imports from context.py,
        # which now references super_admin_runner indirectly).
        from app.agent.runner import _client

        messages: list[dict[str, Any]] = list(history)
        messages.append({"role": "user", "content": user_text})

        tools_used: list[str] = []
        last_text = ""

        try:
            for iteration in range(_MAX_ITERATIONS):
                response = await _client.messages.create(
                    model=self.model,
                    max_tokens=settings.agent_max_tokens,
                    system=_SYSTEM,
                    tools=self._api_tools,
                    messages=messages,
                )

                text_blocks = [b for b in response.content if b.type == "text" and b.text]
                tool_blocks = [b for b in response.content if b.type == "tool_use"]

                if text_blocks:
                    last_text = text_blocks[-1].text

                if not tool_blocks or response.stop_reason == "end_turn":
                    break

                # Append full assistant turn (text + tool_use blocks)
                assistant_content: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools — each in its own isolated session so a failed
                # query on one tool cannot put the session into PendingRollback
                # and corrupt subsequent tool calls in the same turn.
                tool_results: list[dict[str, Any]] = []
                for block in tool_blocks:
                    tools_used.append(block.name)
                    spec = self._tools_by_name.get(block.name)
                    if spec is None:
                        result: Any = {"error": f"Unknown tool: {block.name}"}
                    else:
                        result = await self._run_tool(spec, block.input)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

                messages.append({"role": "user", "content": tool_results})

                log.info(
                    "super_admin_tool_loop",
                    iteration=iteration + 1,
                    tools=[b.name for b in tool_blocks],
                )

        except Exception as exc:
            log.error("super_admin_agent_error", error=str(exc))
            raise AgentError(f"Super-admin agent turn failed: {exc}") from exc

        return last_text or "I couldn't generate a response.", tools_used
