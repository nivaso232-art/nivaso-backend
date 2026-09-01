"""Admin API — agent run telemetry (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.models.agent_run import AgentRun, USD_PER_MTOK_INPUT, USD_PER_MTOK_OUTPUT
from app.models.business import Business

router = APIRouter(prefix="/{slug}/agent-runs", tags=["admin:agent-runs"])


class AgentRunOut(BaseModel):
    id: str
    conversation_id: str | None
    model: str
    effort: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    iterations: int
    tool_calls: int
    stop_reason: str | None
    latency_ms: int | None
    error: str | None
    estimated_cost_usd: float
    created_at: str

    @classmethod
    def from_orm(cls, r: AgentRun) -> "AgentRunOut":
        billed = r.input_tokens + r.cache_creation_tokens * 1.25 + r.cache_read_tokens * 0.1
        cost = round(
            billed / 1_000_000 * USD_PER_MTOK_INPUT
            + r.output_tokens / 1_000_000 * USD_PER_MTOK_OUTPUT,
            4,
        )
        return cls(
            id=str(r.id),
            conversation_id=str(r.conversation_id) if r.conversation_id else None,
            model=r.model,
            effort=r.effort,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
            iterations=r.iterations,
            tool_calls=r.tool_calls,
            stop_reason=r.stop_reason,
            latency_ms=r.latency_ms,
            error=r.error,
            estimated_cost_usd=cost,
            created_at=r.created_at.isoformat(),
        )


@router.get("", response_model=list[AgentRunOut])
async def list_agent_runs(
    slug: str,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[AgentRunOut]:
    stmt = (
        select(AgentRun)
        .where(AgentRun.business_id == business.id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [AgentRunOut.from_orm(r) for r in rows]
