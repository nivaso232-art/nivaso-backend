"""Agent run telemetry."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.models.agent_run import AgentRun
from app.repositories.base import BaseRepository


class AgentRunRepository(BaseRepository[AgentRun]):
    model = AgentRun

    async def list_for_conversation(
        self, conversation_id: uuid.UUID, *, limit: int = 50
    ) -> Sequence[AgentRun]:
        stmt = (
            self._scoped()
            .where(AgentRun.conversation_id == conversation_id)
            .order_by(AgentRun.created_at)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def token_totals(self) -> dict[str, int]:
        """Aggregate usage for this tenant.

        ``cache_read`` is the number that matters most: if it stays at zero
        while ``input`` grows, the cached prefix is being invalidated on every
        turn and the tenant is paying full price for the system prompt each
        time.
        """
        stmt = select(
            func.coalesce(func.sum(AgentRun.input_tokens), 0),
            func.coalesce(func.sum(AgentRun.output_tokens), 0),
            func.coalesce(func.sum(AgentRun.cache_read_tokens), 0),
            func.coalesce(func.sum(AgentRun.cache_creation_tokens), 0),
            func.count(),
        ).where(AgentRun.business_id == self.business_id)

        row = (await self.session.execute(stmt)).one()
        return {
            "input_tokens": int(row[0]),
            "output_tokens": int(row[1]),
            "cache_read_tokens": int(row[2]),
            "cache_creation_tokens": int(row[3]),
            "runs": int(row[4]),
        }
