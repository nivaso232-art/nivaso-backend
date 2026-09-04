"""Entitlement resolution: plan defaults + per-business overrides.

Usage:
    ents = resolve(plan="starter", overrides={"channel.whatsapp": True})
    allowed = check(ents, FeatureFlag.CHANNEL_WHATSAPP)          # True
    limit   = get_limit(ents, FeatureFlag.PRODUCTS_LIMIT)        # 100
    models  = allowed_models(ents)                               # list | None
"""

from __future__ import annotations

from typing import Any

from app.entitlements.flags import PLAN_DEFAULTS, FeatureFlag


def resolve(plan: str, overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge plan defaults with per-business overrides.

    Unknown plans fall back to ``free`` so a future plan rename never
    accidentally grants enterprise access.
    """
    base = PLAN_DEFAULTS.get(plan, PLAN_DEFAULTS["free"]).copy()
    base.update(overrides)
    return base


def check(entitlements: dict[str, Any], flag: str, *, default: bool = False) -> bool:
    """Return the boolean value of a feature flag."""
    val = entitlements.get(flag, default)
    return bool(val)


def get_limit(entitlements: dict[str, Any], flag: str) -> int | None:
    """Return a numeric limit, or None meaning unlimited."""
    return entitlements.get(flag)


def allowed_models(entitlements: dict[str, Any]) -> list[str] | None:
    """Return the list of allowed model IDs, or None meaning all models."""
    return entitlements.get(FeatureFlag.AI_MODELS)


def allowed_tools(entitlements: dict[str, Any]) -> list[str] | None:
    """Return the list of allowed tool names, or None meaning all tools."""
    return entitlements.get(FeatureFlag.AI_TOOLS)


def max_iterations(entitlements: dict[str, Any]) -> int | None:
    """Return the tool-loop cap, or None meaning use the global setting."""
    return entitlements.get(FeatureFlag.AI_MAX_ITERATIONS)
