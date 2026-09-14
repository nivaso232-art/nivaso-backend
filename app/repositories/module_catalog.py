"""Module catalog reads.

Global (non-tenant) repository over ``module_catalog`` — see
``app/models/module_catalog.py`` for what the table represents.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from app.models.module_catalog import ModuleCatalogCategory, ModuleCatalogEntry
from app.repositories.base import GlobalRepository


class ModuleCatalogRepository(GlobalRepository[ModuleCatalogEntry]):
    model = ModuleCatalogEntry

    async def list_active(self) -> Sequence[ModuleCatalogEntry]:
        stmt = (
            select(ModuleCatalogEntry)
            .where(ModuleCatalogEntry.is_active.is_(True))
            .order_by(ModuleCatalogEntry.category, ModuleCatalogEntry.sort_order)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_active_keys(self) -> frozenset[str]:
        rows = await self.list_active()
        return frozenset(row.key for row in rows)

    async def get_signup_requestable_keys(self) -> frozenset[str]:
        """Active catalog keys a business may request at signup time.

        Excludes ``category == 'widget'`` — a widget's own WIDGET_DEPENDENCIES
        module must already be granted before it can be requested (see
        app/entitlements/dashboard_widgets.py), which is never true at signup
        (nothing is granted yet). Widgets only become requestable later, via
        the self-service feature-request endpoint, once their module is on.
        """
        rows = await self.list_active()
        return frozenset(row.key for row in rows if row.category != ModuleCatalogCategory.WIDGET)
