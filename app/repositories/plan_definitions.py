from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan_definition import PlanDefinition


class PlanDefinitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[PlanDefinition]:
        result = await self.session.execute(
            select(PlanDefinition).order_by(PlanDefinition.plan_name)
        )
        return list(result.scalars().all())

    async def get(self, plan_name: str) -> PlanDefinition | None:
        result = await self.session.execute(
            select(PlanDefinition).where(PlanDefinition.plan_name == plan_name)
        )
        return result.scalar_one_or_none()

    async def get_all_as_dict(self) -> dict[str, dict[str, Any]]:
        rows = await self.list_all()
        return {row.plan_name: row.flags for row in rows}

    async def upsert(
        self,
        plan_name: str,
        flags: dict[str, Any],
        *,
        updated_by: str,
    ) -> PlanDefinition:
        row = await self.get(plan_name)
        if row is None:
            row = PlanDefinition(plan_name=plan_name, flags=flags, updated_by=updated_by)
            self.session.add(row)
        else:
            row.flags = flags
            row.updated_by = updated_by
        await self.session.flush()
        return row
