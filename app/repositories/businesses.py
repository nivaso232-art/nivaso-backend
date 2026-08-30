"""Business lookup - the tenant root, so not itself tenant-scoped."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from app.core.errors import NotFoundError
from app.models.business import Business
from app.models.enums import BusinessStatus
from app.repositories.base import GlobalRepository


class BusinessRepository(GlobalRepository[Business]):
    model = Business

    async def get_by_slug(self, slug: str) -> Business | None:
        stmt = select(Business).where(Business.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug_or_raise(self, slug: str) -> Business:
        business = await self.get_by_slug(slug)
        if business is None:
            raise NotFoundError("Business not found", details={"slug": slug})
        return business

    async def get_active_or_raise(self, slug: str) -> Business:
        """Resolve a slug and refuse if the tenant is not active.

        A suspended business must stop transacting immediately - its agent
        should not keep taking orders it cannot fulfil.
        """
        business = await self.get_by_slug_or_raise(slug)
        if business.status is not BusinessStatus.ACTIVE:
            raise NotFoundError(
                "Business is not active",
                details={"slug": slug, "status": business.status.value},
            )
        return business

    async def list_all(self, *, limit: int = 100) -> Sequence[Business]:
        stmt = select(Business).order_by(Business.name).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()
