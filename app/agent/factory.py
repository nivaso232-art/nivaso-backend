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
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import allowed_models, allowed_tools, max_iterations

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

    @property
    def model(self) -> str:
        return getattr(self.primary, "model", "unknown")

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
    *,
    allowed_tool_names: frozenset[str] | None = None,
    max_iterations_override: int | None = None,
) -> Any:
    if provider == "gemini":
        from app.agent.gemini_runner import GeminiAgentRunner
        return GeminiAgentRunner(
            ctx, model=model, extra_tools=extra_tools,
            allowed_tool_names=allowed_tool_names,
            max_iterations_override=max_iterations_override,
        )

    if provider == "groq":
        from app.agent.groq_runner import GroqAgentRunner
        return GroqAgentRunner(
            ctx, model=model, extra_tools=extra_tools,
            allowed_tool_names=allowed_tool_names,
            max_iterations_override=max_iterations_override,
        )

    from app.agent.runner import AgentRunner
    return AgentRunner(
        ctx, model=model, extra_tools=extra_tools,
        allowed_tool_names=allowed_tool_names,
        max_iterations_override=max_iterations_override,
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

async def _load_entitlements(ctx: ToolContext) -> dict[str, Any]:
    """Load resolved entitlements for the current business (cached on ctx)."""
    from app.repositories.entitlements import EntitlementRepository
    repo = EntitlementRepository(ctx.session)
    return await repo.resolved(ctx.business_id)


def build_agent_runner(
    ctx: ToolContext,
    *,
    provider: str | None = None,
    model: str | None = None,
    admin_mode: bool = False,
    entitlements: dict[str, Any] | None = None,
) -> Any:
    """Build the correct runner for *ctx*, honouring plan entitlements and
    per-business model config.

    Resolution order:
      explicit kwargs → business.settings["agent"] → entitlement plan defaults
      → environment defaults.

    Callers pass ``entitlements`` when they have already loaded them (e.g. web
    endpoint). Webhook callers leave it None and the factory loads it lazily.
    """
    extra_tools: Any = ()
    if admin_mode:
        from app.agent.tools.admin_knowledge import ADMIN_TOOLS
        from app.agent.tools.admin_support import ADMIN_SUPPORT_TOOLS
        extra_tools = ADMIN_TOOLS + ADMIN_SUPPORT_TOOLS

    # Per-business model config stored in business.settings["agent"].
    biz_cfg: dict = (ctx.business.settings or {}).get("agent") or {}

    eff_provider = provider or biz_cfg.get("provider") or settings.llm_provider
    eff_model: str | None = model or biz_cfg.get("model") or None

    fb_provider: str | None = biz_cfg.get("fallback_provider")
    fb_model: str | None = biz_cfg.get("fallback_model")

    # Auto-detect provider from the model registry when a model is given but
    # no provider was specified.  This lets callers pass only a model ID and get
    # the right runner automatically (e.g. a Groq model without provider="groq").
    if eff_model and not provider and not biz_cfg.get("provider"):
        from app.agent.models_registry import AVAILABLE_MODELS
        _reg = next((m for m in AVAILABLE_MODELS if m["model"] == eff_model), None)
        if _reg:
            eff_provider = _reg["provider"]

    # -- Entitlement enforcement --------------------------------------------
    allowed_tool_names: frozenset[str] | None = None
    max_iter_override: int | None = None

    if entitlements:
        # Clamp model to the allowed list if one is defined.
        _allowed_models = allowed_models(entitlements)
        if _allowed_models is not None and eff_model not in _allowed_models:
            eff_model = _allowed_models[0] if _allowed_models else None
            # Sync provider with the clamped model so the correct runner is
            # selected.  Without this, a plan that allows only Gemini models
            # would clamp eff_model to "gemini-2.5-flash" but leave
            # eff_provider as "anthropic", sending a Gemini model ID to the
            # Anthropic API — causing a 404/invalid-model error.
            if eff_model:
                from app.agent.models_registry import AVAILABLE_MODELS
                _m = next((m for m in AVAILABLE_MODELS if m["model"] == eff_model), None)
                if _m:
                    eff_provider = _m["provider"]
        if _allowed_models is not None and fb_model not in _allowed_models:
            fb_model = None  # fallback model not on plan — disable it

        # Resolve tool list from the plan.
        _allowed_tools = allowed_tools(entitlements)
        if _allowed_tools is not None:
            allowed_tool_names = frozenset(_allowed_tools)

        # Strip tools whose feature-flag dependency is not satisfied.
        # Works whether ai.tools is an explicit list or None (unrestricted):
        # — explicit list → intersect with the candidate names
        # — None (e.g. Enterprise) → start from the full registry, then filter
        # This is the same one-directional rule as WIDGET_DEPENDENCIES:
        # feature off → tool can never appear, regardless of what ai.tools says.
        from app.entitlements.flags import TOOL_DEPENDENCIES
        from app.agent.registry import TOOLS_BY_NAME

        candidate_names = (
            set(allowed_tool_names)
            if allowed_tool_names is not None
            else set(TOOLS_BY_NAME)
        )
        allowed_tool_names = frozenset(
            name for name in candidate_names
            if not TOOL_DEPENDENCIES.get(name)
            or bool(entitlements.get(TOOL_DEPENDENCIES[name], False))
        )

        max_iter_override = max_iterations(entitlements)

    primary = _build_single_runner(
        eff_provider, eff_model, ctx, extra_tools,
        allowed_tool_names=allowed_tool_names,
        max_iterations_override=max_iter_override,
    )

    if fb_provider and fb_model:
        fallback = _build_single_runner(
            fb_provider, fb_model, ctx, extra_tools,
            allowed_tool_names=allowed_tool_names,
            max_iterations_override=max_iter_override,
        )
        return FallbackAgentRunner(primary, fallback)

    return primary
