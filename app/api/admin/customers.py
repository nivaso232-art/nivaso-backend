"""Admin API — customer management (read-heavy; mutations are rare)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.models.business import Business
from app.models.customer import Customer
from app.repositories.customers import CustomerRepository

router = APIRouter(prefix="/{slug}/customers", tags=["admin:customers"])


class CustomerOut(BaseModel):
    id: str
    name: str | None
    phone: str | None
    email: str | None

    @classmethod
    def from_orm(cls, c: Customer) -> "CustomerOut":
        return cls(
            id=str(c.id),
            name=c.name,
            phone=c.phone,
            email=c.email,
        )


@router.get("", response_model=list[CustomerOut])
async def list_customers(
    slug: str,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[CustomerOut]:
    repo = CustomerRepository(session, business.id)
    customers = await repo.list(limit=limit, offset=offset)
    return [CustomerOut.from_orm(c) for c in customers]


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(
    slug: str,
    customer_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> CustomerOut:
    import uuid
    from app.core.errors import ValidationError
    try:
        cid = uuid.UUID(customer_id)
    except ValueError:
        raise ValidationError("customer_id must be a valid UUID.", details={"customer_id": customer_id})
    repo = CustomerRepository(session, business.id)
    customer = await repo.get_or_raise(cid)
    return CustomerOut.from_orm(customer)
