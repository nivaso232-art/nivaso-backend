"""Generic tenant-scoped repository.

Repositories do data access and nothing else - no pricing, no state
transitions, no provider calls. Those belong in ``app/services``.

The one invariant this layer enforces is tenant isolation: ``business_id`` is
a **required constructor argument**, not an optional filter you can forget.
Every query built here is already scoped, so "an agent read another business's
product" is not a mistake an individual query can make.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Base class for tables carrying ``business_id``."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id

    # -- query construction ----------------------------------------------

    def _scoped(self) -> Select[tuple[ModelT]]:
        """Base SELECT, already filtered to this tenant.

        Every read path starts here. Bypassing it is the only way to leak
        across tenants, which makes that easy to spot in review.
        """
        return select(self.model).where(
            self.model.business_id == self.business_id  # type: ignore[attr-defined]
        )

    # -- reads ------------------------------------------------------------

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        stmt = self._scoped().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_raise(self, entity_id: uuid.UUID) -> ModelT:
        """Fetch or raise 404.

        A row that exists but belongs to another tenant is indistinguishable
        from one that does not exist - deliberately. Returning 403 would
        confirm the id is real, which is a small information leak across a
        tenant boundary.
        """
        entity = await self.get(entity_id)
        if entity is None:
            raise NotFoundError(
                f"{self.model.__name__} not found",
                details={"id": str(entity_id)},
            )
        return entity

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any | None = None,
    ) -> Sequence[ModelT]:
        stmt = self._scoped().limit(limit).offset(offset)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        return (await self.session.execute(stmt)).scalars().all()

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self.model).where(
            self.model.business_id == self.business_id  # type: ignore[attr-defined]
        )
        return (await self.session.execute(stmt)).scalar_one()

    # -- writes -----------------------------------------------------------

    async def add(self, entity: ModelT) -> ModelT:
        """Stage an INSERT and flush so server-side defaults (id) are set.

        No commit - the surrounding :class:`~app.core.uow.UnitOfWork` owns that.
        """
        setattr(entity, "business_id", self.business_id)
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_all(self, entities: Sequence[ModelT]) -> Sequence[ModelT]:
        for entity in entities:
            setattr(entity, "business_id", self.business_id)
        self.session.add_all(list(entities))
        await self.session.flush()
        return entities

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()


class GlobalRepository(Generic[ModelT]):
    """For the handful of tables with no tenant, e.g. ``businesses`` itself and
    ``webhook_events`` (whose ``business_id`` is unknown until parsed)."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        stmt = select(self.model).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_raise(self, entity_id: uuid.UUID) -> ModelT:
        entity = await self.get(entity_id)
        if entity is None:
            raise NotFoundError(
                f"{self.model.__name__} not found", details={"id": str(entity_id)}
            )
        return entity

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity
