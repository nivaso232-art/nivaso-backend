"""Agent runner selection.

Returns the right runner for the configured LLM provider. Both runners share
the same ``run(...)`` surface, so callers (webhooks, the web test endpoint)
don't branch on provider. Imports are lazy so a deployment only needs the SDK
for the provider it actually uses (e.g. no ``google-genai`` install required
when ``LLM_PROVIDER=anthropic``).
"""

from __future__ import annotations

from typing import Any

from app.agent.context import ToolContext
from app.core.config import settings


def build_agent_runner(
    ctx: ToolContext,
    *,
    provider: str | None = None,
    model: str | None = None,
    admin_mode: bool = False,
) -> Any:
    extra_tools: Any = ()
    if admin_mode:
        from app.agent.tools.admin_knowledge import ADMIN_TOOLS
        extra_tools = ADMIN_TOOLS

    effective_provider = provider or settings.llm_provider
    if effective_provider == "gemini":
        from app.agent.gemini_runner import GeminiAgentRunner

        return GeminiAgentRunner(ctx, model=model, extra_tools=extra_tools)

    from app.agent.runner import AgentRunner

    return AgentRunner(ctx, model=model, extra_tools=extra_tools)
