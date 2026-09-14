"""Super-admin API — AI usage (tokens/cost) per business.

Routes:
  GET /super-admin/ai-usage         → current-month usage for every business
  GET /super-admin/ai-usage/{slug}  → same, plus a daily breakdown

Low-traffic admin page: one query per business on the list endpoint is fine,
no need to over-optimize into a single join.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import get_limit
from app.models.agent_run import AgentRun
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository
from app.services.ai_usage import estimate_cost, get_monthly_usage, month_start_utc

router = APIRouter(prefix="/ai-usage", tags=["super-admin:ai-usage"])


@router.get("")
async def list_ai_usage(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    """Current-month AI usage for every business."""
    biz_repo = BusinessRepository(session)
    ent_repo = EntitlementRepository(session)
    businesses = await biz_repo.list_all()

    out: list[dict[str, Any]] = []
    for biz in businesses:
        usage = await get_monthly_usage(session, biz.id)
        entitlements = await ent_repo.resolved(biz.id)
        limit_usd = get_limit(entitlements, FeatureFlag.AI_USAGE_LIMIT_USD)
        out.append({
            "business_id": str(biz.id),
            "business_slug": biz.slug,
            "business_name": biz.name,
            "runs": usage["runs"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cost_usd": usage["cost_usd"],
            "limit_usd": limit_usd,
        })
    return out


@router.get("/{slug}")
async def get_business_ai_usage(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Current-month AI usage for one business, with a daily breakdown."""
    biz_repo = BusinessRepository(session)
    biz = await biz_repo.get_by_slug_or_raise(slug)

    usage = await get_monthly_usage(session, biz.id)
    entitlements = await EntitlementRepository(session).resolved(biz.id)
    limit_usd = get_limit(entitlements, FeatureFlag.AI_USAGE_LIMIT_USD)

    month_start = month_start_utc()
    # cast to Date avoids the date_trunc/bind-parameter issue with asyncpg
    # (same pattern as app/api/admin/metrics.py).
    day_col = cast(AgentRun.created_at, Date)
    day_rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.count().label("runs"),
                func.coalesce(func.sum(AgentRun.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AgentRun.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AgentRun.cache_read_tokens), 0).label("cache_read"),
                func.coalesce(func.sum(AgentRun.cache_creation_tokens), 0).label("cache_creation"),
            )
            .where(
                AgentRun.business_id == biz.id,
                AgentRun.created_at >= month_start,
            )
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    # Fill every day so far this month with zeros so the chart has no gaps.
    today = datetime.now(timezone.utc).date()
    day_map: dict[str, dict[str, Any]] = {}
    d = month_start.date()
    while d <= today:
        day_map[d.strftime("%Y-%m-%d")] = {"date": d.strftime("%Y-%m-%d"), "runs": 0, "cost_usd": 0.0}
        d += timedelta(days=1)

    for r in day_rows:
        key = r.day.strftime("%Y-%m-%d")
        if key in day_map:
            day_map[key]["runs"] = int(r.runs)
            day_map[key]["cost_usd"] = estimate_cost(
                int(r.input_tokens), int(r.output_tokens), int(r.cache_read), int(r.cache_creation)
            )

    return {
        "business_id": str(biz.id),
        "business_slug": biz.slug,
        "business_name": biz.name,
        "runs": usage["runs"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cost_usd": usage["cost_usd"],
        "limit_usd": limit_usd,
        "by_day": list(day_map.values()),
    }
