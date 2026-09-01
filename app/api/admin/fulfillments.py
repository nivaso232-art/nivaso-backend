"""Admin API — fulfillment queue (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.models.business import Business
from app.models.enums import FulfillmentStatus
from app.models.fulfillment import Fulfillment

router = APIRouter(prefix="/{slug}/fulfillments", tags=["admin:fulfillments"])


class FulfillmentOut(BaseModel):
    id: str
    order_id: str
    status: str
    notes: str | None
    fulfilled_at: str | None
    created_at: str

    @classmethod
    def from_orm(cls, f: Fulfillment) -> "FulfillmentOut":
        return cls(
            id=str(f.id),
            order_id=str(f.order_id),
            status=f.status.value,
            notes=f.notes,
            fulfilled_at=f.fulfilled_at.isoformat() if f.fulfilled_at else None,
            created_at=f.created_at.isoformat(),
        )


@router.get("", response_model=list[FulfillmentOut])
async def list_fulfillments(
    slug: str,
    status: FulfillmentStatus | None = None,
    limit: int = 50,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[FulfillmentOut]:
    stmt = (
        select(Fulfillment)
        .where(Fulfillment.business_id == business.id)
        .order_by(Fulfillment.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(Fulfillment.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [FulfillmentOut.from_orm(f) for f in rows]
