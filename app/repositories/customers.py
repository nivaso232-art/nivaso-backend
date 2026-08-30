"""Customer and channel-identity resolution."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.customer import Customer, CustomerChannel
from app.models.enums import Channel
from app.repositories.base import BaseRepository


class CustomerRepository(BaseRepository[Customer]):
    model = Customer

    async def get_by_phone(self, phone: str) -> Customer | None:
        stmt = self._scoped().where(Customer.phone == phone)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_channels(self, customer_id: uuid.UUID) -> Customer | None:
        stmt = (
            self._scoped()
            .where(Customer.id == customer_id)
            .options(selectinload(Customer.channels))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class CustomerChannelRepository(BaseRepository[CustomerChannel]):
    model = CustomerChannel

    async def get_by_external_id(
        self, *, channel: Channel, external_user_id: str
    ) -> CustomerChannel | None:
        stmt = self._scoped().where(
            CustomerChannel.channel == channel,
            CustomerChannel.external_user_id == external_user_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_customer(
        self, *, channel: Channel, external_user_id: str
    ) -> CustomerChannel | None:
        """Resolve a provider handle to the channel row and its customer.

        The hot path for every inbound webhook, so the customer is eager-loaded
        rather than lazy-triggering a second round trip.
        """
        stmt = (
            self._scoped()
            .where(
                CustomerChannel.channel == channel,
                CustomerChannel.external_user_id == external_user_id,
            )
            .options(selectinload(CustomerChannel.customer))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_customer(
        self, customer_id: uuid.UUID
    ) -> list[CustomerChannel]:
        stmt = select(CustomerChannel).where(
            CustomerChannel.business_id == self.business_id,
            CustomerChannel.customer_id == customer_id,
        )
        return list((await self.session.execute(stmt)).scalars().all())
