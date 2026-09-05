"""Business channel credential storage."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_channel import BusinessChannel


class BusinessChannelRepository:
    """Not tenant-scoped: routing lookups span all businesses."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_business(
        self, business_id: uuid.UUID, channel_type: str
    ) -> BusinessChannel | None:
        stmt = select(BusinessChannel).where(
            BusinessChannel.business_id == business_id,
            BusinessChannel.channel_type == channel_type,
            BusinessChannel.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_external_id(
        self, channel_type: str, external_channel_id: str
    ) -> BusinessChannel | None:
        """Route an inbound message to its business by channel identity.

        WhatsApp: external_channel_id = phone_number_id from Meta payload.
        Telegram: external_channel_id = numeric bot_id (token prefix before ':').
        """
        stmt = select(BusinessChannel).where(
            BusinessChannel.channel_type == channel_type,
            BusinessChannel.external_channel_id == external_channel_id,
            BusinessChannel.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_channel_type(
        self, channel_type: str
    ) -> Sequence[BusinessChannel]:
        stmt = select(BusinessChannel).where(
            BusinessChannel.channel_type == channel_type,
            BusinessChannel.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_business(
        self, business_id: uuid.UUID
    ) -> Sequence[BusinessChannel]:
        stmt = select(BusinessChannel).where(
            BusinessChannel.business_id == business_id
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def upsert(
        self,
        *,
        business_id: uuid.UUID,
        channel_type: str,
        external_channel_id: str,
        credentials: dict[str, Any],
    ) -> BusinessChannel:
        """Insert or update the channel config. Returns the live row."""
        stmt = (
            pg_insert(BusinessChannel)
            .values(
                business_id=business_id,
                channel_type=channel_type,
                external_channel_id=external_channel_id,
                credentials=credentials,
                is_active=True,
            )
            .on_conflict_do_update(
                constraint="uq_business_channels_business_id_channel_type",
                set_={
                    "external_channel_id": external_channel_id,
                    "credentials": credentials,
                    "is_active": True,
                    "updated_at": __import__("sqlalchemy", fromlist=["func"]).func.now(),
                },
            )
            .returning(BusinessChannel)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def deactivate(
        self, business_id: uuid.UUID, channel_type: str
    ) -> bool:
        """Soft-delete a channel config. Returns True if a row was found."""
        row = await self.get_for_business(business_id, channel_type)
        if row is None:
            return False
        row.is_active = False
        await self.session.flush()
        return True

    async def delete(
        self, business_id: uuid.UUID, channel_type: str
    ) -> bool:
        """Hard-delete a channel config. Returns True if a row was deleted."""
        row = await self.get_for_business(business_id, channel_type)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True
