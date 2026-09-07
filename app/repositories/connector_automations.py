"""Connector automation flow storage."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector_automation import ConnectorAutomation


class ConnectorAutomationRepository:
    """Not tenant-scoped via BaseRepository — queries span by business_id directly."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_flow(
        self, business_id: uuid.UUID, connector_type: str
    ) -> ConnectorAutomation | None:
        """Return the active automation for this (business, connector_type), or None."""
        stmt = select(ConnectorAutomation).where(
            ConnectorAutomation.business_id == business_id,
            ConnectorAutomation.connector_type == connector_type,
            ConnectorAutomation.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_for_business(
        self, business_id: uuid.UUID, connector_type: str
    ) -> ConnectorAutomation | None:
        """Return the automation row regardless of is_active state."""
        stmt = select(ConnectorAutomation).where(
            ConnectorAutomation.business_id == business_id,
            ConnectorAutomation.connector_type == connector_type,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_business(
        self, business_id: uuid.UUID
    ) -> Sequence[ConnectorAutomation]:
        """Return all automation rows for a business, ordered by connector_type."""
        stmt = (
            select(ConnectorAutomation)
            .where(ConnectorAutomation.business_id == business_id)
            .order_by(ConnectorAutomation.connector_type)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def upsert(
        self,
        business_id: uuid.UUID,
        connector_type: str,
        name: str,
        flow: dict[str, Any],
    ) -> ConnectorAutomation:
        """Insert or update an automation row. Returns the live row."""
        stmt = (
            pg_insert(ConnectorAutomation)
            .values(
                business_id=business_id,
                connector_type=connector_type,
                name=name,
                flow=flow,
                is_active=False,
            )
            .on_conflict_do_update(
                constraint="uq_connector_automations_business_id_connector_type",
                set_={
                    "name": name,
                    "flow": flow,
                    "updated_at": __import__("sqlalchemy", fromlist=["func"]).func.now(),
                },
            )
            .returning(ConnectorAutomation)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def set_active(
        self, business_id: uuid.UUID, connector_type: str, is_active: bool
    ) -> ConnectorAutomation | None:
        """Toggle is_active for the given (business, connector_type). Returns the row or None."""
        row = await self.get_for_business(business_id, connector_type)
        if row is None:
            return None
        row.is_active = is_active
        await self.session.flush()
        return row
