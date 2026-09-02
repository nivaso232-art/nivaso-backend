"""Agent runner selection with per-business model config and automatic fallback.

Resolution order for provider / model:
  1. Explicit ``provider`` / ``model`` kwargs (web chat endpoint, admin tools)
  2. ``business.settings["agent"]["provider|model"]`` — per-business override
  3. ``settings.llm_provider`` / ``settings.agent_model`` — environment defaults

If the business has a ``fallback_provider`` + ``fallback_model`` configured the
runner is wrapped in ``FallbackAgentRunner``, which transparently retries on
the fallback when the primary provider returns a rate-limit or overload error.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agent.context import ToolContext
from app.core.config import settings
from app.core.errors import AgentError

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Retryable-error detection
# ---------------------------------------------------------------------------

def _is_retryable(exc: BaseException | None) -> bool:
    """Return True when the root cause is a transient provider overload / rate-limit."""
    if exc is None:
        return False

    try:
        import anthropic
        if isinstance(exc, anthropic.RateLimitError):
            return True
        if isinstance(exc, anthropic.InternalServerError):
            # Anthropic 529 "overloaded_error" surfaces as InternalServerError
            return getattr(exc, "status_code", 0) == 529
    except ImportError:
        pass

    try:
        from google.api_core import exceptions as gexc
        if isinstance(exc, gexc.ResourceExhausted):
            return True
    except ImportError:
        pass

    return False


# ---------------------------------------------------------------------------
# Fallback wrapper
# ---------------------------------------------------------------------------

class FallbackAgentRunner:
    """Transparent wrapper: tries primary, falls back on retryable provider errors."""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self.primary = primary
        self.fallback = fallback

    async def run(self, **kwargs: Any) -> str:
        try:
            return await self.primary.run(**kwargs)
        except AgentError as exc:
            if not _is_retryable(exc.__cause__):
                raise
            log.warning(
                "agent_primary_rate_limited_falling_back",
                primary_model=getattr(self.primary, "model", "unknown"),
                fallback_model=getattr(self.fallback, "model", "unknown"),
                cause=str(exc.__cause__),
            )
            return await self.fallback.run(**kwargs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_single_runner(
    provider: str,
    model: str | None,
    ctx: ToolContext,
    extra_tools: Any,
) -> Any:
    if provider == "gemini":
        from app.agent.gemini_runner import GeminiAgentRunner
        return GeminiAgentRunner(ctx, model=model, extra_tools=extra_tools)

    from app.agent.runner import AgentRunner
    return AgentRunner(ctx, model=model, extra_tools=extra_tools)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def build_agent_runner(
    ctx: ToolContext,
    *,
    provider: str | None = None,
    model: str | None = None,
    admin_mode: bool = False,
) -> Any:
    """Build the correct runner for *ctx*, honouring per-business model config.

    Callers (webhooks, web endpoint) stay unchanged — they always call this and
    get back an object with a ``run(**kwargs) -> str`` coroutine.
    """
    extra_tools: Any = ()
    if admin_mode:
        from app.agent.tools.admin_knowledge import ADMIN_TOOLS
        extra_tools = ADMIN_TOOLS

    # Per-business override stored in business.settings["agent"]
    biz_cfg: dict = (ctx.business.settings or {}).get("agent") or {}

    eff_provider = provider or biz_cfg.get("provider") or settings.llm_provider
    eff_model: str | None = model or biz_cfg.get("model") or None

    fb_provider: str | None = biz_cfg.get("fallback_provider")
    fb_model: str | None = biz_cfg.get("fallback_model")

    primary = _build_single_runner(eff_provider, eff_model, ctx, extra_tools)

    if fb_provider and fb_model:
        fallback = _build_single_runner(fb_provider, fb_model, ctx, extra_tools)
        return FallbackAgentRunner(primary, fallback)

    return primary
