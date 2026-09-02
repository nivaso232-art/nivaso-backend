"""Feature flag keys and plan defaults.

``FeatureFlag`` constants are the single source of truth for flag names across
the backend (enforcement) and the frontend (rendering). Never use bare strings.

``PLAN_DEFAULTS`` defines what each plan tier grants out of the box. Plans are
generic capability tiers — not tied to any specific industry or client. A dental
clinic and an e-commerce shop both get the same ``starter`` defaults.

Per-business overrides stored in ``business_entitlements.overrides`` are merged
on top at resolve time, so a business can be promoted above its plan on any flag
without moving plans.

``None`` on a list-type flag means *unrestricted* (all values allowed).
``None`` on a numeric flag means *unlimited / use the system setting*.
"""

from __future__ import annotations

from typing import Any


class FeatureFlag:
    """Dotted-namespace constants for every controllable capability."""

    # ── AI ──────────────────────────────────────────────────────────────────
    # List of model IDs the business is allowed to use. None = unrestricted.
    AI_MODELS = "ai.models"
    # Whether the client-admin UI exposes the model-picker at all.
    AI_CUSTOM_MODEL_PICKER = "ai.custom_model_picker"
    # Max tool-loop iterations per agent turn. None = use global setting.
    AI_MAX_ITERATIONS = "ai.max_iterations"
    # Subset of tool names the agent may call. None = all tools.
    AI_TOOLS = "ai.tools"

    # ── Channels ────────────────────────────────────────────────────────────
    CHANNEL_WEB = "channel.web"
    CHANNEL_WHATSAPP = "channel.whatsapp"
    CHANNEL_TELEGRAM = "channel.telegram"
    CHANNEL_PAYMENTS = "channel.payments"

    # ── Catalog & content limits ─────────────────────────────────────────────
    # None = unlimited.
    PRODUCTS_LIMIT = "catalog.products_limit"
    KNOWLEDGE_ARTICLES_LIMIT = "knowledge.articles_limit"

    # ── Operations ───────────────────────────────────────────────────────────
    ORDERS_ENABLED = "orders.enabled"
    SUPPORT_TICKETS_ENABLED = "support.tickets_enabled"
    CREDENTIALS_ENABLED = "credentials.enabled"

    # ── Client-admin UI visibility ───────────────────────────────────────────
    UI_ANALYTICS = "ui.analytics"
    UI_AGENT_RUNS = "ui.agent_runs"
    UI_WEBHOOK_EVENTS = "ui.webhook_events"


# ── Plan defaults ────────────────────────────────────────────────────────────
# Tool names match the ToolSpec.name values in app/agent/registry.py.
# Plans are cumulative in spirit but each is fully explicit here so adding
# a new plan never accidentally inherits from another.

PLAN_DEFAULTS: dict[str, dict[str, Any]] = {
    "free": {
        FeatureFlag.AI_MODELS: ["claude-haiku-4-5-20251001"],
        FeatureFlag.AI_CUSTOM_MODEL_PICKER: False,
        FeatureFlag.AI_MAX_ITERATIONS: 3,
        FeatureFlag.AI_TOOLS: [
            "search_products",
            "get_product",
            "search_knowledge",
            "create_support_ticket",
        ],
        FeatureFlag.CHANNEL_WEB: True,
        FeatureFlag.CHANNEL_WHATSAPP: False,
        FeatureFlag.CHANNEL_TELEGRAM: False,
        FeatureFlag.CHANNEL_PAYMENTS: False,
        FeatureFlag.PRODUCTS_LIMIT: 25,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: 5,
        FeatureFlag.ORDERS_ENABLED: False,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: True,
        FeatureFlag.CREDENTIALS_ENABLED: False,
        FeatureFlag.UI_ANALYTICS: False,
        FeatureFlag.UI_AGENT_RUNS: False,
        FeatureFlag.UI_WEBHOOK_EVENTS: False,
    },
    "starter": {
        FeatureFlag.AI_MODELS: ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
        FeatureFlag.AI_CUSTOM_MODEL_PICKER: False,
        FeatureFlag.AI_MAX_ITERATIONS: 5,
        FeatureFlag.AI_TOOLS: [
            "search_products",
            "get_product",
            "create_order",
            "get_order_status",
            "search_knowledge",
            "create_support_ticket",
        ],
        FeatureFlag.CHANNEL_WEB: True,
        FeatureFlag.CHANNEL_WHATSAPP: False,
        FeatureFlag.CHANNEL_TELEGRAM: False,
        FeatureFlag.CHANNEL_PAYMENTS: False,
        FeatureFlag.PRODUCTS_LIMIT: 100,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: 20,
        FeatureFlag.ORDERS_ENABLED: True,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: True,
        FeatureFlag.CREDENTIALS_ENABLED: False,
        FeatureFlag.UI_ANALYTICS: True,
        FeatureFlag.UI_AGENT_RUNS: False,
        FeatureFlag.UI_WEBHOOK_EVENTS: False,
    },
    "pro": {
        FeatureFlag.AI_MODELS: [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-opus-4-7",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
        ],
        FeatureFlag.AI_CUSTOM_MODEL_PICKER: True,
        FeatureFlag.AI_MAX_ITERATIONS: 8,
        FeatureFlag.AI_TOOLS: [
            "search_products",
            "get_product",
            "create_order",
            "get_order_status",
            "cancel_order",
            "create_payment_link",
            "check_payment_status",
            "search_knowledge",
            "create_support_ticket",
        ],
        FeatureFlag.CHANNEL_WEB: True,
        FeatureFlag.CHANNEL_WHATSAPP: True,
        FeatureFlag.CHANNEL_TELEGRAM: True,
        FeatureFlag.CHANNEL_PAYMENTS: True,
        FeatureFlag.PRODUCTS_LIMIT: 1000,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: 100,
        FeatureFlag.ORDERS_ENABLED: True,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: True,
        FeatureFlag.CREDENTIALS_ENABLED: False,
        FeatureFlag.UI_ANALYTICS: True,
        FeatureFlag.UI_AGENT_RUNS: True,
        FeatureFlag.UI_WEBHOOK_EVENTS: True,
    },
    "enterprise": {
        FeatureFlag.AI_MODELS: None,
        FeatureFlag.AI_CUSTOM_MODEL_PICKER: True,
        FeatureFlag.AI_MAX_ITERATIONS: None,
        FeatureFlag.AI_TOOLS: None,
        FeatureFlag.CHANNEL_WEB: True,
        FeatureFlag.CHANNEL_WHATSAPP: True,
        FeatureFlag.CHANNEL_TELEGRAM: True,
        FeatureFlag.CHANNEL_PAYMENTS: True,
        FeatureFlag.PRODUCTS_LIMIT: None,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: None,
        FeatureFlag.ORDERS_ENABLED: True,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: True,
        FeatureFlag.CREDENTIALS_ENABLED: True,
        FeatureFlag.UI_ANALYTICS: True,
        FeatureFlag.UI_AGENT_RUNS: True,
        FeatureFlag.UI_WEBHOOK_EVENTS: True,
    },
}

VALID_PLANS: frozenset[str] = frozenset(PLAN_DEFAULTS)
