"""Super-admin API — plan tier definition management.

Routes:
  GET    /super-admin/plans               → all plan definitions (DB → fallback)
  GET    /super-admin/plans/hints         → flag metadata (type, description, suggestions, min/max)
  PATCH  /super-admin/plans/{plan_name}   → update a plan's feature flags
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models_registry import AVAILABLE_MODELS
from app.agent.registry import TOOLS
from app.api.deps import get_session
from app.core.errors import NotFoundError
from app.core.uow import UnitOfWork
from app.entitlements.flags import DASHBOARD_WIDGET_CATALOG, PLAN_DEFAULTS, VALID_PLANS
from app.repositories.plan_definitions import PlanDefinitionRepository

router = APIRouter(prefix="/plans", tags=["super-admin:plans"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PlanDefinitionOut(BaseModel):
    plan_name: str
    flags: dict[str, Any]
    updated_by: str | None
    updated_at: str


class PlanFlagsIn(BaseModel):
    flags: dict[str, Any]


class FlagSuggestion(BaseModel):
    value: str
    label: str
    provider: str | None = None


class FlagHint(BaseModel):
    type: str          # "boolean" | "number" | "array"
    description: str
    min: int | None = None
    max: int | None = None
    suggestions: list[FlagSuggestion] | None = None


# ── Hints (built once from the live registries) ───────────────────────────────

def _build_hints() -> dict[str, FlagHint]:
    model_suggestions = [
        FlagSuggestion(
            value=m["model"],
            label=m["label"],
            provider=m["provider"],
        )
        for m in AVAILABLE_MODELS
    ]
    tool_suggestions = [
        FlagSuggestion(value=t.name, label=t.name)
        for t in TOOLS
    ]
    widget_suggestions = [
        FlagSuggestion(value=key, label=label)
        for key, label in DASHBOARD_WIDGET_CATALOG.items()
    ]
    return {
        "ai.models": FlagHint(
            type="array",
            description="AI model IDs the business may use. null = unrestricted (all models).",
            suggestions=model_suggestions,
        ),
        "ai.custom_model_picker": FlagHint(
            type="boolean",
            description="Expose the model-picker in the client admin UI.",
        ),
        "ai.max_iterations": FlagHint(
            type="number",
            description="Max tool-loop iterations per agent turn. null = use global setting.",
            min=1,
            max=50,
        ),
        "ai.tools": FlagHint(
            type="array",
            description="Tool names the agent may call. null = unrestricted (all tools).",
            suggestions=tool_suggestions,
        ),
        "channel.web": FlagHint(
            type="boolean",
            description="Enable the web chat channel.",
        ),
        "channel.whatsapp": FlagHint(
            type="boolean",
            description="Enable the WhatsApp channel.",
        ),
        "channel.telegram": FlagHint(
            type="boolean",
            description="Enable the Telegram channel.",
        ),
        "channel.payments": FlagHint(
            type="boolean",
            description="Enable payment link generation in conversations.",
        ),
        "catalog.products_limit": FlagHint(
            type="number",
            description="Max products in the catalog. null = unlimited.",
            min=1,
            max=100_000,
        ),
        "knowledge.articles_limit": FlagHint(
            type="number",
            description="Max knowledge base articles. null = unlimited.",
            min=1,
            max=10_000,
        ),
        "orders.enabled": FlagHint(
            type="boolean",
            description="Allow order creation and tracking.",
        ),
        "support.tickets_enabled": FlagHint(
            type="boolean",
            description="Allow support ticket creation.",
        ),
        "credentials.enabled": FlagHint(
            type="boolean",
            description="Allow storing customer-specific credentials (e.g. loyalty IDs).",
        ),
        "ui.agent_runs": FlagHint(
            type="boolean",
            description="Show the Agent Runs log in the client admin UI.",
        ),
        "ui.webhook_events": FlagHint(
            type="boolean",
            description="Show the Webhook Events log in the client admin UI.",
        ),
        "ui.dashboard_customize": FlagHint(
            type="boolean",
            description="Allow the business to pick which dashboard widgets to show.",
        ),
        "ui.dashboard_widgets": FlagHint(
            type="array",
            description="Dashboard widgets this plan may enable. null = unrestricted (all 13 widgets). Controls the entire dashboard — basics and advanced alike.",
            suggestions=widget_suggestions,
        ),
    }


_HINTS: dict[str, FlagHint] = _build_hints()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=dict[str, dict[str, Any]])
async def list_plans(
    session: AsyncSession = Depends(get_session),
) -> dict[str, dict[str, Any]]:
    """Return all plan tier definitions. DB-stored flags override hardcoded defaults flag-by-flag."""
    try:
        db_plans = await PlanDefinitionRepository(session).get_all_as_dict()
        if db_plans:
            return {
                plan: {**flags, **db_plans.get(plan, {})}
                for plan, flags in PLAN_DEFAULTS.items()
            }
    except Exception:
        pass
    return PLAN_DEFAULTS


@router.get("/hints", response_model=dict[str, FlagHint])
async def get_plan_hints() -> dict[str, FlagHint]:
    """Return type, description, valid suggestions, and min/max for every feature flag."""
    return _HINTS


@router.patch("/{plan_name}", response_model=PlanDefinitionOut)
async def update_plan(
    plan_name: str,
    body: PlanFlagsIn,
    session: AsyncSession = Depends(get_session),
) -> PlanDefinitionOut:
    """Persist updated feature flags for a plan tier."""
    if plan_name not in VALID_PLANS:
        raise NotFoundError(f"Unknown plan '{plan_name}'. Valid: {sorted(VALID_PLANS)}")

    repo = PlanDefinitionRepository(session)
    async with UnitOfWork(session):
        plan_def = await repo.upsert(plan_name, body.flags, updated_by="super-admin")
        # Refresh inside the UoW so server-generated updated_at is loaded
        # before the session expires attributes on commit.
        await session.refresh(plan_def)

    return PlanDefinitionOut(
        plan_name=plan_def.plan_name,
        flags=plan_def.flags,
        updated_by=plan_def.updated_by,
        updated_at=plan_def.updated_at.isoformat(),
    )
