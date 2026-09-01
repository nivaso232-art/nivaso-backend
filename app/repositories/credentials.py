"""Credential vault reads/writes, including atomic slot allocation."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.models.credential import ProductCredential
from app.models.enums import CredentialStatus
from app.repositories.base import BaseRepository


class CredentialRepository(BaseRepository[ProductCredential]):
    model = ProductCredential

    async def list_for_product(
        self, product_id: uuid.UUID
    ) -> Sequence[ProductCredential]:
        stmt = (
            self._scoped()
            .where(ProductCredential.product_id == product_id)
            .order_by(ProductCredential.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def count_free_slots(self, product_id: uuid.UUID) -> int:
        """Sum of remaining slots across active credentials for a product."""
        stmt = select(
            func.coalesce(
                func.sum(ProductCredential.capacity - ProductCredential.allocated), 0
            )
        ).where(
            ProductCredential.business_id == self.business_id,
            ProductCredential.product_id == product_id,
            ProductCredential.status == CredentialStatus.ACTIVE,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def acquire_free_slot(
        self, product_id: uuid.UUID
    ) -> ProductCredential | None:
        """Lock and return one active credential with a free slot, or None.

        Orders by ``allocated`` descending so partly-used shared accounts fill
        up before a fresh one is opened (maximising reuse). ``FOR UPDATE SKIP
        LOCKED`` means two concurrent deliveries never hand the same slot to two
        different orders — the second skips the locked row and takes another.
        """
        stmt = (
            self._scoped()
            .where(
                ProductCredential.product_id == product_id,
                ProductCredential.status == CredentialStatus.ACTIVE,
                ProductCredential.allocated < ProductCredential.capacity,
            )
            .order_by(
                ProductCredential.allocated.desc(), ProductCredential.created_at
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
