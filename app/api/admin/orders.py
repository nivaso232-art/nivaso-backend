"""Admin API — order management (read-only list + detail)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_business, get_session
from app.core.errors import ForbiddenError
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import check, resolve
from app.models.business import Business
from app.models.enums import OrderStatus
from app.models.order import Order, OrderItem
from app.repositories.entitlements import EntitlementRepository
from app.repositories.orders import OrderRepository

router = APIRouter(prefix="/{slug}/orders", tags=["admin:orders"])


async def _require_orders_module(business: Business, session: AsyncSession) -> None:
    ent = await EntitlementRepository(session).get_or_create(business.id)
    if not check(resolve(ent.plan, ent.overrides), FeatureFlag.ORDERS_ENABLED):
        raise ForbiddenError("Orders are not enabled for this business.")


class OrderItemOut(BaseModel):
    id: str
    product_name: str
    product_sku: str | None
    unit_price: str
    quantity: int
    total: str


class OrderOut(BaseModel):
    id: str
    reference: str
    status: str
    currency: str
    subtotal: str
    discount: str
    total: str
    customer_id: str
    conversation_id: str | None
    items: list[OrderItemOut]
    created_at: str

    @classmethod
    def from_orm(cls, o: Order) -> "OrderOut":
        return cls(
            id=str(o.id),
            reference=o.reference,
            status=o.status.value,
            currency=o.currency,
            subtotal=str(o.subtotal),
            discount=str(o.discount),
            total=str(o.total),
            customer_id=str(o.customer_id),
            conversation_id=str(o.conversation_id) if o.conversation_id else None,
            items=[
                OrderItemOut(
                    id=str(item.id),
                    product_name=item.product_name,
                    product_sku=item.product_sku,
                    unit_price=str(item.unit_price),
                    quantity=item.quantity,
                    total=str(item.total),
                )
                for item in o.items
            ],
            created_at=o.created_at.isoformat(),
        )


@router.get("", response_model=list[OrderOut])
async def list_orders(
    slug: str,
    status: OrderStatus | None = None,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[OrderOut]:
    await _require_orders_module(business, session)
    stmt = (
        select(Order)
        .where(Order.business_id == business.id)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(Order.status == status)
    orders = (await session.execute(stmt)).scalars().all()
    return [OrderOut.from_orm(o) for o in orders]
