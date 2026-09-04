from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_admin import BusinessAdmin


class BusinessAdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, business_id: uuid.UUID, username: str, password_hash: str) -> BusinessAdmin:
        admin = BusinessAdmin(business_id=business_id, username=username, password_hash=password_hash)
        self.session.add(admin)
        await self.session.flush()
        return admin

    async def get_by_username(self, username: str) -> BusinessAdmin | None:
        result = await self.session.execute(
            select(BusinessAdmin).where(BusinessAdmin.username == username)
        )
        return result.scalar_one_or_none()

    async def get_by_business_id(self, business_id: uuid.UUID) -> BusinessAdmin | None:
        result = await self.session.execute(
            select(BusinessAdmin).where(BusinessAdmin.business_id == business_id)
        )
        return result.scalar_one_or_none()
