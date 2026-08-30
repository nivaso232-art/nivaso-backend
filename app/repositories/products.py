"""Product reads, including the no-embeddings search path."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, literal, or_, select

from app.models.enums import ProductStatus
from app.models.product import Product
from app.repositories.base import BaseRepository

# Below this trigram similarity a "match" is noise. Tuned for short product
# names; raise it if searches start returning unrelated items.
TRGM_THRESHOLD = 0.25


class ProductRepository(BaseRepository[Product]):
    model = Product

    async def get_by_sku(self, sku: str) -> Product | None:
        stmt = self._scoped().where(Product.sku == sku)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active(
        self, *, category: str | None = None, limit: int = 50, offset: int = 0
    ) -> Sequence[Product]:
        stmt = self._scoped().where(Product.status == ProductStatus.ACTIVE)
        if category:
            stmt = stmt.where(Product.category == category)
        stmt = stmt.order_by(Product.name).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 10,
        include_unavailable: bool = False,
    ) -> Sequence[Product]:
        """Full-text search with a trigram fallback.

        Two passes, in order:

        1. ``websearch_to_tsquery`` against the generated ``search_doc``. This
           handles multi-word queries and quoted phrases, and - unlike
           ``to_tsquery`` - never raises on punctuation a customer typed.
        2. If that returns nothing, trigram similarity on ``name``. This is the
           typo path: "gta v" or "gt a5" still finds "GTA 5".

        Fallback rather than union because a real FTS hit is always better than
        a fuzzy one; mixing them lets a high-similarity irrelevant name outrank
        an exact keyword match.
        """
        cleaned = query.strip()
        if not cleaned:
            return []

        tsquery = func.websearch_to_tsquery(literal("english"), cleaned)

        stmt = (
            self._scoped()
            .where(Product.search_doc.op("@@")(tsquery))
            .order_by(func.ts_rank(Product.search_doc, tsquery).desc(), Product.name)
            .limit(limit)
        )
        if not include_unavailable:
            stmt = stmt.where(Product.status == ProductStatus.ACTIVE)
        if category:
            stmt = stmt.where(Product.category == category)

        results = (await self.session.execute(stmt)).scalars().all()
        if results:
            return results

        return await self._search_fuzzy(
            cleaned,
            category=category,
            limit=limit,
            include_unavailable=include_unavailable,
        )

    async def _search_fuzzy(
        self,
        query: str,
        *,
        category: str | None,
        limit: int,
        include_unavailable: bool,
    ) -> Sequence[Product]:
        """pg_trgm similarity on name. Requires the pg_trgm extension."""
        similarity = func.similarity(Product.name, query)

        stmt = (
            self._scoped()
            .where(similarity > TRGM_THRESHOLD)
            .order_by(similarity.desc())
            .limit(limit)
        )
        if not include_unavailable:
            stmt = stmt.where(Product.status == ProductStatus.ACTIVE)
        if category:
            stmt = stmt.where(Product.category == category)

        return (await self.session.execute(stmt)).scalars().all()

    async def get_many(self, product_ids: Sequence[uuid.UUID]) -> Sequence[Product]:
        """Batch fetch, still tenant-scoped.

        Used by order creation: one query for every line item, and any id
        belonging to another business simply does not come back - so the
        missing-id check in ``order_service`` doubles as the tenant check.
        """
        if not product_ids:
            return []
        stmt = self._scoped().where(Product.id.in_(product_ids))
        return (await self.session.execute(stmt)).scalars().all()

    async def list_categories(self) -> Sequence[str]:
        stmt = (
            select(Product.category)
            .where(
                Product.business_id == self.business_id,
                Product.category.is_not(None),
            )
            .distinct()
            .order_by(Product.category)
        )
        return [row for row in (await self.session.execute(stmt)).scalars().all() if row]
