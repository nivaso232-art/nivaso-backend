"""Catalog tools: ``search_products`` and ``get_product``.

``get_product`` is the authoritative price source the agent should quote from.
Even so, the price it returns is only ever *displayed* - ``create_order``
re-reads it from the database independently (rule 1), so a stale quote in chat
cannot become a stale price on an order.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, integer_prop, schema, string_prop
from app.core.errors import ValidationError
from app.models.product import Product


def _serialize(product: Product) -> dict[str, Any]:
    """Shape a product for the model.

    ``price`` is a string, not a float. Serialising a Decimal through JSON as a
    float invites 228.99999 into a price quote; a string round-trips exactly
    and the model reads it back fine.
    """
    return {
        "product_id": str(product.id),
        "name": product.name,
        "description": product.description,
        "price": str(product.price),
        "currency": product.currency,
        "category": product.category,
        "status": product.status.value,
        "available": product.is_sellable,
        "attributes": product.metadata_,
    }


async def search_products(
    ctx: ToolContext,
    query: str,
    category: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    products = await ctx.catalog.search_products(
        query, category=category, limit=limit or 5
    )
    return {
        "query": query,
        "count": len(products),
        "products": [_serialize(product) for product in products],
        # Told explicitly rather than left implicit: an empty list otherwise
        # tends to produce "we don't sell that" when the truth is "that search
        # found nothing", and those are different answers.
        "note": (
            "No products matched. Try different keywords or ask the customer "
            "to clarify - do not tell them the item does not exist."
            if not products
            else None
        ),
    }


async def get_product(ctx: ToolContext, product_id: str) -> dict[str, Any]:
    try:
        parsed = uuid.UUID(product_id)
    except ValueError as exc:
        raise ValidationError(
            "product_id must be a UUID returned by search_products.",
            details={"product_id": product_id},
        ) from exc

    product = await ctx.catalog.get_product_or_raise(parsed)
    return _serialize(product)


async def compare_products(
    ctx: ToolContext, product_ids: list[str]
) -> dict[str, Any]:
    """Fetch 2–4 products side-by-side so the customer can choose."""
    if len(product_ids) < 2 or len(product_ids) > 4:
        return {"error": "Provide between 2 and 4 product_id values to compare."}

    products = []
    for raw_id in product_ids:
        try:
            pid = uuid.UUID(str(raw_id))
        except ValueError:
            return {"error": f"Invalid product_id: {raw_id!r}. Use IDs from search_products."}
        try:
            product = await ctx.catalog.get_product_or_raise(pid)
            products.append(_serialize(product))
        except Exception:
            return {"error": f"Product {raw_id!r} not found."}

    return {"count": len(products), "products": products}


COMPARE_PRODUCTS = ToolSpec(
    name="compare_products",
    description=(
        "Fetch 2 to 4 products side-by-side (price, description, attributes) so "
        "the customer can decide which one to buy. Use this when a customer asks "
        "'which is better, X or Y?' after a search. All product_id values must come "
        "from a prior search_products or get_product call."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "product_ids": {
                "type": "array",
                "description": "2 to 4 product_id UUIDs from search_products.",
                "minItems": 2,
                "maxItems": 4,
                "items": {"type": "string"},
            }
        },
        "required": ["product_ids"],
        "additionalProperties": False,
    },
    handler=compare_products,
)


async def check_product_availability(
    ctx: ToolContext, product_id: str
) -> dict[str, Any]:
    """Report how many credential slots are in stock for a product."""
    try:
        pid = uuid.UUID(str(product_id))
    except ValueError:
        return {"error": f"Invalid product_id: {product_id!r}."}

    try:
        product = await ctx.catalog.get_product_or_raise(pid)
    except Exception:
        return {"error": f"Product {product_id!r} not found."}

    try:
        slots = await ctx.credentials.free_slots(pid)
        return {
            "product_id": product_id,
            "product_name": product.name,
            "available_slots": slots,
            "in_stock": slots > 0,
        }
    except Exception:
        return {
            "product_id": product_id,
            "product_name": product.name,
            "available_slots": None,
            "note": "Availability tracking is not configured for this product.",
        }


CHECK_PRODUCT_AVAILABILITY = ToolSpec(
    name="check_product_availability",
    description=(
        "Check whether a product is in stock (has available credential slots) before "
        "the customer commits to buying. Use this when the customer asks 'is it "
        "available?' or 'do you have stock?' to avoid an out-of-stock post-order."
    ),
    input_schema=schema(
        properties={
            "product_id": string_prop(
                "The product_id UUID from search_products or get_product."
            ),
        }
    ),
    handler=check_product_availability,
)


SEARCH_PRODUCTS = ToolSpec(
    name="search_products",
    description=(
        "Search this business's catalog by keyword. Use this whenever the "
        "customer asks whether something is available, asks for a price, or "
        "describes what they want. Returns product_id values you must pass to "
        "other tools - never invent one. If the customer writes in Tamil, "
        "Tanglish, or mixed language, translate the product name to English "
        "before searching."
    ),
    input_schema=schema(
        properties={
            "query": string_prop(
                "Product name or keywords, in English. "
                'e.g. "GTA 5", "action game", "2bhk apartment".'
            ),
            "category": string_prop(
                "Optional category filter. Omit unless the customer named one.",
                nullable=True,
            ),
            "limit": integer_prop(
                "Maximum results to return. Defaults to 5.",
                minimum=1,
                maximum=20,
                nullable=True,
            ),
        }
    ),
    handler=search_products,
)

GET_PRODUCT = ToolSpec(
    name="get_product",
    description=(
        "Fetch the current, authoritative details for one product by its "
        "product_id. Call this before quoting a price you are not certain of. "
        "Always quote the price this tool returns - never a price you remember "
        "from earlier in the conversation."
    ),
    input_schema=schema(
        properties={
            "product_id": string_prop(
                "The product_id (a UUID) from a previous search_products result."
            ),
        }
    ),
    handler=get_product,
)
