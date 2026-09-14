"""Admin API — AI usage (tokens/cost) breakdown for a single business.

Distinct from ``app/api/super_admin/ai_usage.py`` (cross-tenant, super-admin
only) and from the per-widget dashboard endpoints in
``app/api/admin/dashboard_widgets.py``. This is the business's own admin
asking "where is my AI spend coming from" — broken down by channel
(WhatsApp/Telegram/Web/Instagram) and by customer, for the current calendar
month.

Reuses ``app/services/ai_usage.py`` for the totals and the one shared cost
formula (``estimate_cost``) rather than duplicating either. As in
``app/api/super_admin/ai_usage.py``, the nonlinear cost formula is applied in
Python after SQL does the token aggregation per group — it does not push
into the SQL itself.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.models.agent_run import AgentRun
from app.models.business import Business
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.services.ai_usage import estimate_cost, get_monthly_usage, month_start_utc

router = APIRouter(prefix="/{slug}/ai-usage", tags=["admin:ai-usage"])


class ChannelUsageOut(BaseModel):
    channel: str
    runs: int
    cost_usd: float


class CustomerUsageOut(BaseModel):
    customer_id: str
    customer_name: str | None
    runs: int
    cost_usd: float


class AiUsageOut(BaseModel):
    total: dict[str, Any]  # {runs, cost_usd, input_tokens, output_tokens}
    by_channel: list[ChannelUsageOut]
    by_customer: list[CustomerUsageOut]


@router.get("", response_model=AiUsageOut)
async def get_ai_usage(
    slug: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> AiUsageOut:
    """Current-month AI usage for this business, broken down by channel and customer.

    ``total`` matches ``get_monthly_usage`` exactly (every ``AgentRun`` this
    month, no join). ``by_channel``/``by_customer`` inner-join to
    ``Conversation`` (and ``Customer``), so an ``AgentRun`` with no
    ``conversation_id`` contributes to ``total`` but not to either breakdown.
    """
    month_start = month_start_utc()

    usage = await get_monthly_usage(session, business.id)
    total = {
        "runs": usage["runs"],
        "cost_usd": usage["cost_usd"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
    }

    channel_rows = (
        await session.execute(
            select(
                Conversation.channel.label("channel"),
                func.count().label("runs"),
                func.coalesce(func.sum(AgentRun.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AgentRun.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AgentRun.cache_read_tokens), 0).label("cache_read"),
                func.coalesce(func.sum(AgentRun.cache_creation_tokens), 0).label("cache_creation"),
            )
            .join(Conversation, AgentRun.conversation_id == Conversation.id)
            .where(
                AgentRun.business_id == business.id,
                AgentRun.created_at >= month_start,
            )
            .group_by(Conversation.channel)
        )
    ).all()

    by_channel = [
        ChannelUsageOut(
            channel=str(r.channel),
            runs=int(r.runs),
            cost_usd=estimate_cost(
                int(r.input_tokens), int(r.output_tokens), int(r.cache_read), int(r.cache_creation)
            ),
        )
        for r in channel_rows
    ]

    customer_rows = (
        await session.execute(
            select(
                Conversation.customer_id.label("customer_id"),
                Customer.name.label("customer_name"),
                func.count().label("runs"),
                func.coalesce(func.sum(AgentRun.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AgentRun.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AgentRun.cache_read_tokens), 0).label("cache_read"),
                func.coalesce(func.sum(AgentRun.cache_creation_tokens), 0).label("cache_creation"),
            )
            .join(Conversation, AgentRun.conversation_id == Conversation.id)
            .join(Customer, Conversation.customer_id == Customer.id)
            .where(
                AgentRun.business_id == business.id,
                AgentRun.created_at >= month_start,
            )
            .group_by(Conversation.customer_id, Customer.name)
        )
    ).all()

    by_customer = [
        CustomerUsageOut(
            customer_id=str(r.customer_id),
            customer_name=r.customer_name,
            runs=int(r.runs),
            cost_usd=estimate_cost(
                int(r.input_tokens), int(r.output_tokens), int(r.cache_read), int(r.cache_creation)
            ),
        )
        for r in customer_rows
    ]
    by_customer.sort(key=lambda c: c.cost_usd, reverse=True)
    by_customer = by_customer[:10]

    return AiUsageOut(total=total, by_channel=by_channel, by_customer=by_customer)
