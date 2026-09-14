"""Single source of truth for per-widget dashboard access.

A gated widget is just another entry in the module catalog now (category
``widget``) — access is granted per-business via a super-admin override or an
approved ``FeatureRequest``, exactly like ``module.*``/``channel.*`` flags.
There is no plan-list gate any more (``FeatureFlag.UI_DASHBOARD_WIDGETS`` is
no longer consulted for access — only ``ui.dashboard_customize`` for the
*selection* feature, handled in ``app/api/admin/dashboard.py``).

Both the widget *selector* (``GET/PATCH /{slug}/dashboard-config``) and each
individual widget *data* endpoint (``GET /{slug}/dashboard/widgets/{key}``)
must call :func:`widget_allowed` — never duplicate this logic.
"""

from __future__ import annotations

from typing import Any

from app.entitlements.flags import DASHBOARD_BASIC_WIDGET_KEYS, WIDGET_DEPENDENCIES


def widget_allowed(key: str, resolved_flags: dict[str, Any]) -> bool:
    """Return True if this business may see the widget identified by ``key``.

    A widget is allowed if:
      - it's one of the always-on default basics, OR its own flag key is
        truthy in ``resolved_flags`` (gated widgets use their catalog key
        literally as the feature-flag key, e.g. ``"stat.orders_today"``),
      AND
      - its ``WIDGET_DEPENDENCIES`` entry (if any) is also truthy.
    """
    own_ok = key in DASHBOARD_BASIC_WIDGET_KEYS or bool(resolved_flags.get(key, False))
    if not own_ok:
        return False
    dep = WIDGET_DEPENDENCIES.get(key)
    return dep is None or bool(resolved_flags.get(dep, False))
