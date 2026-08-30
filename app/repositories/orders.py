"""Order reads."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enums import TERMINAL_ORDER_STATUSES, OrderStatus
from app.models.order import Order, OrderItem
from app.repositories.base import BaseRepository


class OrderRepository(BaseRepository[Order]):
    model = Order

    async def get_by_reference(self, reference: str) -> Order | None:
        stmt = (
            self._scoped()
            .where(Order.reference == reference)
            .options(selectinload(Order.items))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_items(self, order_id: uuid.UUID) -> Order | None:
        stmt = (
            self._scoped()
            .where(Order.id == order_id)
            .options(selectinload(Order.items), selectinload(Order.payments))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_customer(
        self, customer_id: uuid.UUID, *, limit: int = 20, open_only: bool = False
    ) -> Sequence[Order]:
        """Order history, newest first.

        Powers edge case 20 - a customer returning three days later asking
        "where's my GTA 5?" - so the items are eager-loaded; the answer always
        needs to name the product.
        """
        stmt = (
            self._scoped()
            .where(Order.customer_id == customer_id)
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        if open_only:
            stmt = stmt.where(Order.status.notin_(list(TERMINAL_ORDER_STATUSES)))
        return (await self.session.execute(stmt)).scalars().all()

    async def get_latest_open_for_customer(
        self, customer_id: uuid.UUID
    ) -> Order | None:
        """The order the customer most likely means when they say "it".

        Used when the agent calls ``get_order_status`` with no reference - a
        customer rarely quotes ORD-2608-7F3K9Q, they just say "my order".
        """
        stmt = (
            self._scoped()
            .where(
                Order.customer_id == customer_id,
                Order.status.notin_(list(TERMINAL_ORDER_STATUSES)),
            )
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_status(
        self, status: OrderStatus, *, limit: int = 100
    ) -> Sequence[Order]:
        stmt = (
            self._scoped()
            .where(Order.status == status)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def reference_exists(self, reference: str) -> bool:
        """Pre-check used by the generate-and-retry loop in ``core.ids``."""
        stmt = select(Order.id).where(
            Order.business_id == self.business_id, Order.reference == reference
        )
        return (await self.session.execute(stmt)).first() is not None


class OrderItemRepository:
    """Not tenant-scoped: ``order_items`` has no ``business_id``.

    Reaching an item always goes through its order, which is scoped - so
    tenant isolation is preserved one hop up.
    """

    def __init__(self, session: object) -> None:
        self.session = session  # AsyncSession; typed loosely to avoid a cycle

    async def add_all(self, items: Sequence[OrderItem]) -> Sequence[OrderItem]:
        self.session.add_all(list(items))  # type: ignore[attr-defined]
        await self.session.flush()  # type: ignore[attr-defined]
        return items
