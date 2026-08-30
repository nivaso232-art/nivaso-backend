"""Catalog reads for the agent and the admin API.

The authoritative price source. Every quote the agent gives should originate
from :meth:`CatalogService.get_product` or :meth:`search_products` - and every
price the *order* uses is re-read independently in ``order_service``, so even
a stale quote in chat cannot become a stale price in the ledger.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.product import Product
from app.repositories.products import ProductRepository

log = structlog.get_logger(__name__)

# Search results go into an LLM prompt. Five is enough for the model to pick or
# ask a clarifying question; twenty is mostly wasted input tokens.
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20


class CatalogService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.products = ProductRepository(session, business_id)

    async def search_products(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> Sequence[Product]:
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        results = await self.products.search(query, category=category, limit=limit)
        log.info(
            "catalog_search",
            query=query,
            category=category,
            hits=len(results),
        )
        return results

    async def get_product_or_raise(self, product_id: uuid.UUID) -> Product:
        """Fetch one product. 404 if it is missing *or* another tenant's."""
        product = await self.products.get(product_id)
        if product is None:
            raise NotFoundError(
                "Product not found.", details={"product_id": str(product_id)}
            )
        return product

    async def get_by_sku_or_raise(self, sku: str) -> Product:
        product = await self.products.get_by_sku(sku)
        if product is None:
            raise NotFoundError("Product not found.", details={"sku": sku})
        return product

    async def list_active(
        self,
        *,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Product]:
        return await self.products.list_active(
            category=category, limit=limit, offset=offset
        )

    async def list_categories(self) -> Sequence[str]:
        """Used in the cached system prompt so the agent knows what this
        business sells without a tool call for the obvious question."""
        return await self.products.list_categories()
