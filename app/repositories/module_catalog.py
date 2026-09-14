"""Module catalog reads.

Global (non-tenant) repository over ``module_catalog`` — see
``app/models/module_catalog.py`` for what the table represents.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from app.models.module_catalog import ModuleCatalogEntry
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
