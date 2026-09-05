"""Admin metrics — single-endpoint dashboard aggregation.

All queries run in one request so the dashboard page makes exactly one
network call instead of six.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.models.agent_run import USD_PER_MTOK_INPUT, USD_PER_MTOK_OUTPUT, AgentRun
from app.models.business import Business
from app.models.conversation import Conversation
from app.models.customer import Customer, CustomerChannel
from app.models.enums import (
    Channel,
    ConversationStatus,
    FulfillmentStatus,
    KnowledgeStatus,
    PaymentStatus,
    ProductStatus,
    TicketStatus,
)
from app.models.fulfillment import Fulfillment
from app.models.knowledge import Knowledge
from app.models.payment import Payment
from app.models.product import Product
from app.models.support_ticket import SupportTicket

router = APIRouter(tags=["admin:metrics"])

_OPEN_STATUSES = {TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.WAITING_CUSTOMER}


def _bucket_by_week(daily: list[tuple[date, Decimal]], weeks: int = 8) -> list[dict[str, Any]]:
    """Bucket daily amounts into the last N Monday-starting weeks."""
    today = datetime.now(timezone.utc).date()
    this_week_start = today - timedelta(days=today.weekday())
    starts = [this_week_start - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    sums = {s: Decimal("0") for s in starts}
    for day, amount in daily:
        week_start = day - timedelta(days=day.weekday())
        if week_start in sums:
            sums[week_start] += amount
    return [{"label": f"Wk of {s.strftime('%b %d')}", "amount": float(sums[s])} for s in starts]


def _bucket_by_month(daily: list[tuple[date, Decimal]], months: int = 12) -> list[dict[str, Any]]:
    """Bucket daily amounts into the last N calendar months."""
    today = datetime.now(timezone.utc).date()
    keys: list[tuple[int, int]] = []
    y, m = today.year, today.month
    for i in range(months - 1, -1, -1):
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        keys.append((yy, mm))
    sums = {k: Decimal("0") for k in keys}
    for day, amount in daily:
        key = (day.year, day.month)
        if key in sums:
            sums[key] += amount
    return [
        {"label": date(yy, mm, 1).strftime("%b %Y"), "amount": float(sums[(yy, mm)])}
        for yy, mm in keys
    ]


def _bucket_by_year(daily: list[tuple[date, Decimal]], years: int = 5) -> list[dict[str, Any]]:
    """Bucket daily amounts into the last N calendar years."""
    today = datetime.now(timezone.utc).date()
    keys = [today.year - i for i in range(years - 1, -1, -1)]
    sums = {k: Decimal("0") for k in keys}
    for day, amount in daily:
        if day.year in sums:
            sums[day.year] += amount
    return [{"label": str(k), "amount": float(sums[k])} for k in keys]


def _estimate_cost(input_tok: int, output_tok: int, cache_read: int, cache_creation: int) -> float:
    billed = input_tok + cache_creation * 1.25 + cache_read * 0.1
    return round(
        billed / 1_000_000 * USD_PER_MTOK_INPUT
        + output_tok / 1_000_000 * USD_PER_MTOK_OUTPUT,
        4,
    )


@router.get("/{slug}/metrics")
async def get_metrics(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Aggregate dashboard metrics for a business in one round-trip."""
    biz_id = business.id
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)

    # ── Tickets ──────────────────────────────────────────────────────────────
    ticket_rows = (
        await session.execute(
            select(SupportTicket.status, func.count().label("n"))
            .where(SupportTicket.business_id == biz_id)
            .group_by(SupportTicket.status)
        )
    ).all()
    tickets_by_status: dict[str, int] = {str(r.status): r.n for r in ticket_rows}

    priority_rows = (
        await session.execute(
            select(SupportTicket.priority, func.count().label("n"))
            .where(
                SupportTicket.business_id == biz_id,
                SupportTicket.status.notin_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
            )
            .group_by(SupportTicket.priority)
        )
    ).all()
    tickets_by_priority: dict[str, int] = {str(r.priority): r.n for r in priority_rows}

    total_open = sum(v for k, v in tickets_by_status.items() if k in {s.value for s in _OPEN_STATUSES})

    # ── Products ─────────────────────────────────────────────────────────────
    product_rows = (
        await session.execute(
            select(Product.status, func.count().label("n"))
            .where(Product.business_id == biz_id)
            .group_by(Product.status)
        )
    ).all()
    products_by_status: dict[str, int] = {str(r.status): r.n for r in product_rows}

    # ── Knowledge ─────────────────────────────────────────────────────────────
    knowledge_rows = (
        await session.execute(
            select(Knowledge.status, func.count().label("n"))
            .where(Knowledge.business_id == biz_id)
            .group_by(Knowledge.status)
        )
    ).all()
    knowledge_by_status: dict[str, int] = {str(r.status): r.n for r in knowledge_rows}

    # ── Customers ─────────────────────────────────────────────────────────────
    total_customers: int = await session.scalar(
        select(func.count()).where(Customer.business_id == biz_id)
    ) or 0
    new_customers_7d: int = await session.scalar(
        select(func.count()).where(
            Customer.business_id == biz_id,
            Customer.created_at >= week_start,
        )
    ) or 0

    # ── Agent runs (today) ────────────────────────────────────────────────────
    today_row = (
        await session.execute(
            select(
                func.count().label("count"),
                func.coalesce(func.sum(AgentRun.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AgentRun.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AgentRun.cache_read_tokens), 0).label("cache_read"),
                func.coalesce(func.sum(AgentRun.cache_creation_tokens), 0).label("cache_creation"),
                func.coalesce(func.avg(AgentRun.latency_ms), 0).label("avg_latency"),
            ).where(
                AgentRun.business_id == biz_id,
                AgentRun.created_at >= today_start,
            )
        )
    ).one()

    # ── Agent runs by day (last 7 days) ───────────────────────────────────────
    # cast to Date avoids the date_trunc/bind-parameter issue with asyncpg
    day_col = cast(AgentRun.created_at, Date)
    day_rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.count().label("count"),
                func.coalesce(
                    func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0
                ).label("tokens"),
            )
            .where(
                AgentRun.business_id == biz_id,
                AgentRun.created_at >= week_start,
            )
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    # Fill every day in the window with zeros so the chart has no gaps.
    day_map: dict[str, dict[str, Any]] = {
        (week_start + timedelta(days=i)).strftime("%Y-%m-%d"): {
            "date": (week_start + timedelta(days=i)).strftime("%b %d"),
            "runs": 0,
            "tokens": 0,
        }
        for i in range(7)
    }
    for r in day_rows:
        key = r.day.strftime("%Y-%m-%d")
        day_map[key]["runs"] = int(r.count)
        day_map[key]["tokens"] = int(r.tokens)

    # ── Sessions ──────────────────────────────────────────────────────────────
    active_sessions: int = await session.scalar(
        select(func.count()).where(
            Conversation.business_id == biz_id,
            Conversation.status == ConversationStatus.ACTIVE,
            Conversation.channel == Channel.WEB,
        )
    ) or 0
    total_sessions: int = await session.scalar(
        select(func.count()).where(
            CustomerChannel.business_id == biz_id,
            CustomerChannel.channel == Channel.WEB,
        )
    ) or 0

    # ── Products delivered (all-time) ────────────────────────────────────────
    products_delivered: int = await session.scalar(
        select(func.count()).where(
            Fulfillment.business_id == biz_id,
            Fulfillment.status == FulfillmentStatus.DELIVERED,
        )
    ) or 0

    # ── Revenue (successful payments, bucketed for the week/month/year toggle) ─
    revenue_day_col = cast(Payment.created_at, Date)
    revenue_rows = (
        await session.execute(
            select(
                revenue_day_col.label("day"),
                func.coalesce(func.sum(Payment.amount), 0).label("amount"),
            )
            .where(
                Payment.business_id == biz_id,
                Payment.status == PaymentStatus.SUCCESS,
            )
            .group_by(revenue_day_col)
        )
    ).all()
    revenue_daily = [(r.day, Decimal(r.amount)) for r in revenue_rows]

    return {
        "tickets": {
            "by_status": tickets_by_status,
            "by_priority": tickets_by_priority,
            "total_open": total_open,
        },
        "products": {
            "by_status": products_by_status,
            "total_active": products_by_status.get(ProductStatus.ACTIVE.value, 0),
            "total": sum(products_by_status.values()),
        },
        "knowledge": {
            "by_status": knowledge_by_status,
            "total_published": knowledge_by_status.get(KnowledgeStatus.PUBLISHED.value, 0),
        },
        "customers": {
            "total": total_customers,
            "new_last_7d": new_customers_7d,
        },
        "agent_runs": {
            "today": {
                "count": int(today_row.count),
                "tokens": int(today_row.input_tokens) + int(today_row.output_tokens),
                "avg_latency_ms": int(today_row.avg_latency or 0),
                "estimated_cost_usd": _estimate_cost(
                    int(today_row.input_tokens),
                    int(today_row.output_tokens),
                    int(today_row.cache_read),
                    int(today_row.cache_creation),
                ),
            },
            "by_day": list(day_map.values()),
        },
        "sessions": {
            "active": active_sessions,
            "total": total_sessions,
        },
        "products_delivered": products_delivered,
        "revenue": {
            "currency": "INR",
            "by_week": _bucket_by_week(revenue_daily),
            "by_month": _bucket_by_month(revenue_daily),
            "by_year": _bucket_by_year(revenue_daily),
        },
    }
