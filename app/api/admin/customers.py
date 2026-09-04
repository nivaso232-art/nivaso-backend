"""Admin API — customer management (read-heavy; mutations are rare).

Customer list excludes admin test sessions. When a business admin uses the
Agent Chat tester in the admin portal the request goes through /web/chat with
admin_mode=True, and web.py prefixes the external_user_id with "__admin__"
before storing it — regardless of what session ID the frontend sent.  This
means the customer list filter is enforced by the backend and cannot be
bypassed by renaming the session ID in the UI.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.models.business import Business
from app.models.customer import Customer, CustomerChannel
from app.models.enums import Channel
from app.repositories.customers import CustomerChannelRepository, CustomerRepository

router = APIRouter(prefix="/{slug}/customers", tags=["admin:customers"])

# Prefix applied by web.py to all admin_mode=True sessions.
# The customer list excludes any customer whose only web channel uses this prefix.
_ADMIN_PREFIX = "__admin__"


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
    """Return real customers — excludes admin Agent Chat test sessions.

    Admin test sessions are stored with external_user_id prefixed "__admin__"
    (set by web.py when admin_mode=True, regardless of what the frontend sends).
    A customer is shown only when they have at least one non-WEB channel, OR
    when their web channel ID does NOT start with the admin prefix.
    The test records stay in the DB so conversation history works in the
    admin chat panel — they are simply excluded from this customer-facing list.
    """
    # Subquery A: customer has at least one non-WEB channel → definitely real
    has_real_channel = (
        select(func.count(CustomerChannel.id))
        .where(CustomerChannel.business_id == business.id)
        .where(CustomerChannel.customer_id == Customer.id)
        .where(CustomerChannel.channel != Channel.WEB)
        .correlate(Customer)
        .scalar_subquery()
    )

    # Subquery B: customer has a WEB channel not marked as admin test
    has_real_web_id = (
        select(func.count(CustomerChannel.id))
        .where(CustomerChannel.business_id == business.id)
        .where(CustomerChannel.customer_id == Customer.id)
        .where(CustomerChannel.channel == Channel.WEB)
        .where(~CustomerChannel.external_user_id.like(f"{_ADMIN_PREFIX}%"))
        .correlate(Customer)
        .scalar_subquery()
    )

    stmt = (
        select(Customer)
        .where(Customer.business_id == business.id)
        .where(or_(has_real_channel > 0, has_real_web_id > 0))
        .order_by(Customer.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [CustomerOut.from_orm(c) for c in result.scalars().all()]


class ChannelOut(BaseModel):
    id: str
    channel: str
    external_user_id: str
    display_name: str | None


@router.get("/{customer_id}/channels", response_model=list[ChannelOut])
async def list_customer_channels(
    slug: str,
    customer_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[ChannelOut]:
    import uuid
    from app.core.errors import ValidationError
    try:
        cid = uuid.UUID(customer_id)
    except ValueError:
        raise ValidationError("customer_id must be a valid UUID.", details={"customer_id": customer_id})
    repo = CustomerChannelRepository(session, business.id)
    channels = await repo.list_for_customer(cid)
    return [
        ChannelOut(
            id=str(ch.id),
            channel=ch.channel.value,
            external_user_id=ch.external_user_id,
            display_name=ch.display_name,
        )
        for ch in channels
    ]


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
