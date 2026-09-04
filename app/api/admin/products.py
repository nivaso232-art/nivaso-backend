"""Admin API — product catalog management."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ForbiddenError
from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import get_limit, resolve
from app.models.business import Business
from app.models.enums import ProductStatus
from app.repositories.entitlements import EntitlementRepository
from app.models.product import Product
from app.repositories.products import ProductRepository

router = APIRouter(prefix="/{slug}/products", tags=["admin:products"])


# -- schemas ------------------------------------------------------------------

class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sku: str | None
    name: str
    description: str | None
    price: str
    currency: str
    status: str
    category: str | None
    attributes: dict[str, Any]

    @classmethod
    def from_orm(cls, p: Product) -> "ProductOut":
        return cls(
            id=str(p.id),
            sku=p.sku,
            name=p.name,
            description=p.description,
            price=str(p.price),
            currency=p.currency,
            status=p.status.value,
            category=p.category,
            attributes=p.metadata_,
        )


class CreateProductIn(BaseModel):
    name: str
    description: str | None = None
    price: Decimal
    currency: str = "INR"
    sku: str | None = None
    category: str | None = None
    status: ProductStatus = ProductStatus.ACTIVE
    attributes: dict[str, Any] = {}


class UpdateProductIn(BaseModel):
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    sku: str | None = None
    category: str | None = None
    status: ProductStatus | None = None
    attributes: dict[str, Any] | None = None


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[ProductOut])
async def list_products(
    slug: str,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[ProductOut]:
    repo = ProductRepository(session, business.id)
    products = await repo.list_active(category=category, limit=limit, offset=offset)
    return [ProductOut.from_orm(p) for p in products]


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    slug: str,
    body: CreateProductIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ProductOut:
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(business.id)
    limit = get_limit(resolve(ent.plan, ent.overrides), FeatureFlag.PRODUCTS_LIMIT)
    if limit is not None:
        repo = ProductRepository(session, business.id)
        count = await repo.count()
        if count >= limit:
            raise ForbiddenError(
                f"Product limit reached ({limit}). Upgrade your plan to add more.",
                details={"limit": limit, "current": count, "flag": FeatureFlag.PRODUCTS_LIMIT},
            )
    product = Product(
        name=body.name,
        description=body.description,
        price=body.price,
        currency=body.currency,
        sku=body.sku,
        category=body.category,
        status=body.status,
        metadata_=body.attributes,
    )
    async with UnitOfWork(session):
        repo = ProductRepository(session, business.id)
        await repo.add(product)
    return ProductOut.from_orm(product)


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(
    slug: str,
    product_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ProductOut:
    import uuid
    from app.core.errors import ValidationError
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise ValidationError("product_id must be a valid UUID.", details={"product_id": product_id})
    repo = ProductRepository(session, business.id)
    product = await repo.get_or_raise(pid)
    return ProductOut.from_orm(product)


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    slug: str,
    product_id: str,
    body: UpdateProductIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ProductOut:
    import uuid
    from app.core.errors import ValidationError
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise ValidationError("product_id must be a valid UUID.", details={"product_id": product_id})
    repo = ProductRepository(session, business.id)
    product = await repo.get_or_raise(pid)

    async with UnitOfWork(session):
        if body.name is not None:
            product.name = body.name
        if body.description is not None:
            product.description = body.description
        if body.price is not None:
            product.price = body.price
        if body.currency is not None:
            product.currency = body.currency
        if body.sku is not None:
            product.sku = body.sku
        if body.category is not None:
            product.category = body.category
        if body.status is not None:
            product.status = body.status
        if body.attributes is not None:
            product.metadata_ = body.attributes

    return ProductOut.from_orm(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_product(
    slug: str,
    product_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Archive a product (soft delete). Order history referencing it is preserved."""
    import uuid
    from app.core.errors import ValidationError
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise ValidationError("product_id must be a valid UUID.", details={"product_id": product_id})
    repo = ProductRepository(session, business.id)
    product = await repo.get_or_raise(pid)
    async with UnitOfWork(session):
        product.status = ProductStatus.ARCHIVED
