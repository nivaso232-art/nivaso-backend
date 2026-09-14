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

Dashboard widget notes
──────────────────────
``DASHBOARD_WIDGET_CATALOG`` is the complete ordered set of widget keys. The
former "basic" widgets (products, customers, tickets, revenue) are now first-
class catalog entries governed by ``ui.dashboard_widgets`` just like the
advanced ones — there is no hardcoded always-on tier any more.

``WIDGET_DEPENDENCIES`` maps a widget key to the feature flag that must be
truthy before the widget may appear, regardless of what the plan allows. This
is enforced server-side on every GET/PATCH so it cannot be bypassed.
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
    # Monthly USD spend cap, super-admin set. None = unlimited. Crossing this
    # triggers an in-app notification (see app/services/ai_usage.py) - it is
    # purely an observability/alerting cap, not an enforcement gate that
    # blocks agent turns.
    AI_USAGE_LIMIT_USD = "ai.usage_limit_usd"

    # ── Channels ────────────────────────────────────────────────────────────
    CHANNEL_WEB = "channel.web"
    CHANNEL_WHATSAPP = "channel.whatsapp"
    CHANNEL_TELEGRAM = "channel.telegram"
    CHANNEL_INSTAGRAM = "channel.instagram"
    CHANNEL_PAYMENTS = "channel.payments"

    # ── Catalog & content limits ─────────────────────────────────────────────
    # None = unlimited.
    PRODUCTS_LIMIT = "catalog.products_limit"
    KNOWLEDGE_ARTICLES_LIMIT = "knowledge.articles_limit"

    # ── Modules (catalog/CRM feature areas an admin can toggle per business) ────
    MODULE_PRODUCTS = "module.products"
    MODULE_SERVICES = "module.services"
    MODULE_OFFERS = "module.offers"
    MODULE_COUPONS = "module.coupons"
    MODULE_APPOINTMENTS = "module.appointments"
    MODULE_CUSTOMERS = "module.customers"
    # Admin-defined custom field *schemas* for products/services/offers/coupons.
    # Gates only the ability to define/edit field definitions — entities keep
    # validating against whatever definitions already exist regardless.
    MODULE_CUSTOM_FIELDS = "module.custom_fields"

    # ── Operations ───────────────────────────────────────────────────────────
    ORDERS_ENABLED = "orders.enabled"
    SUPPORT_TICKETS_ENABLED = "support.tickets_enabled"
    CREDENTIALS_ENABLED = "credentials.enabled"

    # ── Client-admin UI visibility ───────────────────────────────────────────
    UI_AGENT_RUNS = "ui.agent_runs"
    UI_WEBHOOK_EVENTS = "ui.webhook_events"

    # ── Dashboard customization ──────────────────────────────────────────────
    # Whether this business can pick which widgets to show.
    UI_DASHBOARD_CUSTOMIZE = "ui.dashboard_customize"
    # Subset of DASHBOARD_WIDGET_CATALOG keys this plan may enable.
    # None = all widgets allowed (unrestricted).
    UI_DASHBOARD_WIDGETS = "ui.dashboard_widgets"


# Complete ordered catalog of dashboard widget keys. Order here determines
# display order on the dashboard (basics first, advanced after). Keys are
# stable identifiers — never rename one without a migration to rewrite
# existing selections in business.settings["dashboard"]["widgets"].
DASHBOARD_WIDGET_CATALOG: dict[str, str] = {
    # ── Formerly always-on basics — now plan-controlled like everything else ─
    "stat.products":           "Active Products",
    "stat.customers":          "Customers",
    "stat.open_tickets":       "Open Tickets",
    "stat.products_delivered": "Products Delivered",
    "chart.revenue":           "Revenue",
    # ── New defaults ──────────────────────────────────────────────────────────
    "list.needs_attention":    "Needs Attention",
    "gauge.plan_usage":        "Plan Usage",
    # ── Advanced ────────────────────────────────────────────────────────────
    "stat.active_sessions":    "Active Sessions",
    "stat.agent_runs_today":   "Agent Runs Today",
    "stat.published_articles": "Published Articles",
    "chart.agent_runs_7d":     "Agent Runs (7-day)",
    "chart.ticket_status":     "Ticket Status",
    "chart.product_catalog":   "Product Catalog Breakdown",
    "chart.token_usage":       "Token Usage (7-day)",
    "chart.ticket_priority":   "Open Ticket Priority",
    # ── New gated widgets ─────────────────────────────────────────────────────
    "stat.orders_today":            "Orders Today",
    "donut.order_status":           "Order Status Breakdown",
    "bar.top_products":             "Top Selling Products",
    "funnel.sales":                 "Sales Funnel",
    "stat.active_coupons":          "Active Coupons",
    "stat.active_offers":           "Active Offers",
    "stat.appointments_upcoming":   "Upcoming Appointments",
}

# The subset that used to be hardcoded/always-on, plus the 2 new defaults.
# Kept as a frozenset so the dashboard endpoint can detect pre-unification
# saved selections and migrate them transparently without a data migration.
DASHBOARD_BASIC_WIDGET_KEYS: frozenset[str] = frozenset({
    "stat.products",
    "stat.customers",
    "stat.open_tickets",
    "stat.products_delivered",
    "chart.revenue",
    "list.needs_attention",
    "gauge.plan_usage",
})

# Widget key → feature flag that must be truthy for the widget to appear.
# Enforced server-side on every GET/PATCH; cannot be bypassed by plan config.
# Absence means no dependency (widget shows whenever the plan allows it).
WIDGET_DEPENDENCIES: dict[str, str] = {
    "stat.products":           FeatureFlag.MODULE_PRODUCTS,
    "stat.customers":          FeatureFlag.MODULE_CUSTOMERS,
    "stat.products_delivered": FeatureFlag.ORDERS_ENABLED,
    "stat.open_tickets":       FeatureFlag.SUPPORT_TICKETS_ENABLED,
    "stat.agent_runs_today":   FeatureFlag.UI_AGENT_RUNS,
    "chart.agent_runs_7d":     FeatureFlag.UI_AGENT_RUNS,
    "chart.token_usage":       FeatureFlag.UI_AGENT_RUNS,
    "chart.ticket_status":     FeatureFlag.SUPPORT_TICKETS_ENABLED,
    "chart.ticket_priority":   FeatureFlag.SUPPORT_TICKETS_ENABLED,
    # ── New dependencies ──────────────────────────────────────────────────────
    "chart.product_catalog":      FeatureFlag.MODULE_PRODUCTS,
    "chart.revenue":               FeatureFlag.CHANNEL_PAYMENTS,
    "stat.orders_today":           FeatureFlag.ORDERS_ENABLED,
    "donut.order_status":          FeatureFlag.ORDERS_ENABLED,
    "bar.top_products":            FeatureFlag.ORDERS_ENABLED,
    "funnel.sales":                FeatureFlag.ORDERS_ENABLED,
    "stat.active_coupons":         FeatureFlag.MODULE_COUPONS,
    "stat.active_offers":          FeatureFlag.MODULE_OFFERS,
    "stat.appointments_upcoming":  FeatureFlag.MODULE_APPOINTMENTS,
}

# Tool name → feature flag that must be truthy for the tool to be injected.
# Enforced at runner build-time in factory.py; cannot be bypassed via ai.tools
# or per-business overrides. Absence means no dependency.
#
# This mirrors WIDGET_DEPENDENCIES: the ai.tools plan list sets the *maximum*
# available tools; feature flags determine which of those are actually active.
# Both must say yes for a tool to appear in the agent's tool list.
TOOL_DEPENDENCIES: dict[str, str] = {
    # ── Orders ───────────────────────────────────────────────────────────────
    "create_order":               FeatureFlag.ORDERS_ENABLED,
    "list_my_orders":             FeatureFlag.ORDERS_ENABLED,
    "get_order_status":           FeatureFlag.ORDERS_ENABLED,
    "cancel_order":               FeatureFlag.ORDERS_ENABLED,
    "get_fulfillment_details":    FeatureFlag.ORDERS_ENABLED,
    # ── Payments ─────────────────────────────────────────────────────────────
    "create_payment_link":        FeatureFlag.CHANNEL_PAYMENTS,
    "check_payment_status":       FeatureFlag.CHANNEL_PAYMENTS,
    "get_order_payment_history":  FeatureFlag.CHANNEL_PAYMENTS,
    "retry_payment":              FeatureFlag.CHANNEL_PAYMENTS,
    # ── Credentials ──────────────────────────────────────────────────────────
    "get_my_credentials":         FeatureFlag.CREDENTIALS_ENABLED,
    "check_product_availability": FeatureFlag.CREDENTIALS_ENABLED,
    # ── Support tickets ───────────────────────────────────────────────────────
    "create_support_ticket":      FeatureFlag.SUPPORT_TICKETS_ENABLED,
    "list_open_tickets":          FeatureFlag.SUPPORT_TICKETS_ENABLED,
    "update_support_ticket":      FeatureFlag.SUPPORT_TICKETS_ENABLED,
}


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
            # ── discovery ──────────────────────────────────────────────────
            "search_products",
            "get_product",
            # ── orders ─────────────────────────────────────────────────────
            "create_order",
            "get_order_status",
            "cancel_order",
            # ── payments ───────────────────────────────────────────────────
            "create_payment_link",
            "check_payment_status",
            # ── knowledge ──────────────────────────────────────────────────
            "search_knowledge",
            # ── support ────────────────────────────────────────────────────
            "create_support_ticket",
            "list_open_tickets",
            "update_support_ticket",
            # ── delivery ───────────────────────────────────────────────────
            "get_my_credentials",
            # ── platform (mandatory baseline, no dependency gate) ──────────
            "request_feature_access",
            "check_feature_request_status",
        ],
        FeatureFlag.AI_USAGE_LIMIT_USD: None,
        FeatureFlag.CHANNEL_WEB: False,
        FeatureFlag.CHANNEL_WHATSAPP: False,
        FeatureFlag.CHANNEL_TELEGRAM: False,
        FeatureFlag.CHANNEL_INSTAGRAM: False,
        FeatureFlag.CHANNEL_PAYMENTS: False,
        FeatureFlag.PRODUCTS_LIMIT: 25,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: 5,
        FeatureFlag.MODULE_PRODUCTS: False,
        FeatureFlag.MODULE_SERVICES: False,
        FeatureFlag.MODULE_OFFERS: False,
        FeatureFlag.MODULE_COUPONS: False,
        FeatureFlag.MODULE_APPOINTMENTS: False,
        FeatureFlag.MODULE_CUSTOMERS: False,
        FeatureFlag.MODULE_CUSTOM_FIELDS: False,
        FeatureFlag.ORDERS_ENABLED: False,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: False,
        FeatureFlag.CREDENTIALS_ENABLED: False,
        FeatureFlag.UI_AGENT_RUNS: False,
        FeatureFlag.UI_WEBHOOK_EVENTS: False,
        FeatureFlag.UI_DASHBOARD_CUSTOMIZE: False,
        FeatureFlag.UI_DASHBOARD_WIDGETS: [
            "stat.products",
            "stat.customers",
            "stat.open_tickets",
            "stat.products_delivered",
            "chart.revenue",
        ],
    },
    "starter": {
        FeatureFlag.AI_MODELS: ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
        FeatureFlag.AI_CUSTOM_MODEL_PICKER: False,
        FeatureFlag.AI_MAX_ITERATIONS: 5,
        FeatureFlag.AI_TOOLS: [
            # ── discovery ──────────────────────────────────────────────────
            "search_products",
            "get_product",
            "compare_products",           # new: Starter+
            # ── orders ─────────────────────────────────────────────────────
            "create_order",
            "list_my_orders",             # new: Starter+
            "get_order_status",
            "cancel_order",
            # ── payments ───────────────────────────────────────────────────
            "create_payment_link",
            "check_payment_status",
            # ── knowledge ──────────────────────────────────────────────────
            "search_knowledge",
            "get_full_article",           # new: Starter+
            # ── support ────────────────────────────────────────────────────
            "create_support_ticket",
            "list_open_tickets",
            "update_support_ticket",
            # ── delivery ───────────────────────────────────────────────────
            "get_my_credentials",
            # ── platform (mandatory baseline) ──────────────────────────────
            "request_feature_access",
            "check_feature_request_status",
        ],
        FeatureFlag.AI_USAGE_LIMIT_USD: None,
        FeatureFlag.CHANNEL_WEB: True,
        FeatureFlag.CHANNEL_WHATSAPP: False,
        FeatureFlag.CHANNEL_TELEGRAM: False,
        FeatureFlag.CHANNEL_INSTAGRAM: False,
        FeatureFlag.CHANNEL_PAYMENTS: False,
        FeatureFlag.PRODUCTS_LIMIT: 100,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: 20,
        FeatureFlag.MODULE_PRODUCTS: True,
        FeatureFlag.MODULE_SERVICES: True,
        FeatureFlag.MODULE_OFFERS: False,
        FeatureFlag.MODULE_COUPONS: False,
        FeatureFlag.MODULE_APPOINTMENTS: False,
        FeatureFlag.MODULE_CUSTOMERS: True,
        FeatureFlag.MODULE_CUSTOM_FIELDS: False,
        FeatureFlag.ORDERS_ENABLED: True,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: True,
        FeatureFlag.CREDENTIALS_ENABLED: False,
        FeatureFlag.UI_AGENT_RUNS: False,
        FeatureFlag.UI_WEBHOOK_EVENTS: False,
        FeatureFlag.UI_DASHBOARD_CUSTOMIZE: False,
        FeatureFlag.UI_DASHBOARD_WIDGETS: [
            "stat.products",
            "stat.customers",
            "stat.open_tickets",
            "stat.products_delivered",
            "chart.revenue",
        ],
    },
    "pro": {
        FeatureFlag.AI_MODELS: [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-opus-4-7",
            "gemini-3.6-flash",
            "gemini-3.1-pro-preview",
            # Groq — fast inference
            "llama-3.3-70b-versatile",
            "llama3-groq-70b-8192-tool-use-preview",
            "openai/gpt-oss-120b",
            "deepseek-r1-distill-llama-70b",
        ],
        FeatureFlag.AI_CUSTOM_MODEL_PICKER: True,
        FeatureFlag.AI_MAX_ITERATIONS: 8,
        FeatureFlag.AI_TOOLS: [
            # ── discovery ──────────────────────────────────────────────────
            "search_products",
            "get_product",
            "compare_products",
            "check_product_availability", # new: Pro+
            # ── orders ─────────────────────────────────────────────────────
            "create_order",
            "list_my_orders",
            "get_order_status",
            "cancel_order",
            "get_fulfillment_details",    # new: Pro+
            # ── payments ───────────────────────────────────────────────────
            "create_payment_link",
            "check_payment_status",
            "get_order_payment_history",  # new: Pro+
            "retry_payment",              # new: Pro+
            # ── knowledge ──────────────────────────────────────────────────
            "search_knowledge",
            "get_full_article",
            # ── support ────────────────────────────────────────────────────
            "create_support_ticket",
            "list_open_tickets",
            "update_support_ticket",
            # ── delivery ───────────────────────────────────────────────────
            "get_my_credentials",
            # ── platform (mandatory baseline) ──────────────────────────────
            "request_feature_access",
            "check_feature_request_status",
        ],
        FeatureFlag.AI_USAGE_LIMIT_USD: None,
        FeatureFlag.CHANNEL_WEB: True,
        FeatureFlag.CHANNEL_WHATSAPP: True,
        FeatureFlag.CHANNEL_TELEGRAM: True,
        FeatureFlag.CHANNEL_INSTAGRAM: True,
        FeatureFlag.CHANNEL_PAYMENTS: True,
        FeatureFlag.PRODUCTS_LIMIT: 1000,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: 100,
        FeatureFlag.MODULE_PRODUCTS: True,
        FeatureFlag.MODULE_SERVICES: True,
        FeatureFlag.MODULE_OFFERS: True,
        FeatureFlag.MODULE_COUPONS: True,
        FeatureFlag.MODULE_APPOINTMENTS: True,
        FeatureFlag.MODULE_CUSTOMERS: True,
        FeatureFlag.MODULE_CUSTOM_FIELDS: True,
        FeatureFlag.ORDERS_ENABLED: True,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: True,
        FeatureFlag.CREDENTIALS_ENABLED: False,
        FeatureFlag.UI_AGENT_RUNS: True,
        FeatureFlag.UI_WEBHOOK_EVENTS: True,
        FeatureFlag.UI_DASHBOARD_CUSTOMIZE: True,
        FeatureFlag.UI_DASHBOARD_WIDGETS: [
            "stat.products",
            "stat.customers",
            "stat.open_tickets",
            "stat.products_delivered",
            "chart.revenue",
            "stat.active_sessions",
            "stat.agent_runs_today",
            "stat.published_articles",
            "chart.agent_runs_7d",
            "chart.ticket_status",
        ],
    },
    "enterprise": {
        FeatureFlag.AI_MODELS: None,
        FeatureFlag.AI_CUSTOM_MODEL_PICKER: True,
        FeatureFlag.AI_MAX_ITERATIONS: None,
        FeatureFlag.AI_TOOLS: None,
        FeatureFlag.AI_USAGE_LIMIT_USD: None,
        FeatureFlag.CHANNEL_WEB: True,
        FeatureFlag.CHANNEL_WHATSAPP: True,
        FeatureFlag.CHANNEL_TELEGRAM: True,
        FeatureFlag.CHANNEL_INSTAGRAM: True,
        FeatureFlag.CHANNEL_PAYMENTS: True,
        FeatureFlag.PRODUCTS_LIMIT: None,
        FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT: None,
        FeatureFlag.MODULE_PRODUCTS: True,
        FeatureFlag.MODULE_SERVICES: True,
        FeatureFlag.MODULE_OFFERS: True,
        FeatureFlag.MODULE_COUPONS: True,
        FeatureFlag.MODULE_APPOINTMENTS: True,
        FeatureFlag.MODULE_CUSTOMERS: True,
        FeatureFlag.MODULE_CUSTOM_FIELDS: True,
        FeatureFlag.ORDERS_ENABLED: True,
        FeatureFlag.SUPPORT_TICKETS_ENABLED: True,
        FeatureFlag.CREDENTIALS_ENABLED: True,
        FeatureFlag.UI_AGENT_RUNS: True,
        FeatureFlag.UI_WEBHOOK_EVENTS: True,
        FeatureFlag.UI_DASHBOARD_CUSTOMIZE: True,
        FeatureFlag.UI_DASHBOARD_WIDGETS: None,
    },
}

VALID_PLANS: frozenset[str] = frozenset(PLAN_DEFAULTS)

# Used as a fallback when the business_entitlements table does not yet exist
# (migrations pending). Grants the same access as enterprise so no functionality
# is lost for businesses that existed before the entitlement system was added.
MIGRATION_PENDING_FLAGS: dict[str, Any] = PLAN_DEFAULTS["enterprise"].copy()
