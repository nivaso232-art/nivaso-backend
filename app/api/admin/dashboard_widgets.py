"""Admin API — per-widget dashboard data.

Replaces the single aggregated ``GET /{slug}/metrics`` endpoint (left
untouched in ``app/api/admin/metrics.py``) with one independently-gated
endpoint per widget: ``GET /{slug}/dashboard/widgets/{widget_key}``. Each
widget is fetched (and gated) on its own so the frontend only pays for the
data it actually renders, and access can be granted per-business per-widget
through the same module-catalog/entitlement-request system used for
``module.*``/``channel.*`` flags (see ``app/entitlements/dashboard_widgets.py``
for the single gating source of truth).

Query logic below is adapted directly from ``app/api/admin/metrics.py`` —
see that file for the original combined-endpoint version.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.metrics import _OPEN_STATUSES, _bucket_by_week
from app.api.deps import get_business, get_session
from app.core.dates import parse_date_range
from app.core.errors import ForbiddenError, NotFoundError
from app.entitlements.dashboard_widgets import widget_allowed
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import check, get_limit, resolve
from app.models.agent_run import AgentRun
from app.models.appointment import Appointment, AppointmentStatus
from app.models.business import Business
from app.models.conversation import Conversation
from app.models.coupon import Coupon, CouponStatus
from app.models.customer import Customer, CustomerChannel
from app.models.enums import (
    Channel,
    ConversationStatus,
    FulfillmentStatus,
    KnowledgeStatus,
    PaymentStatus,
    ProductStatus,
    TicketPriority,
    TicketStatus,
)
from app.models.fulfillment import Fulfillment
from app.models.knowledge import Knowledge
from app.models.offer import Offer, OfferStatus
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.product import Product
from app.models.service import Service, ServiceStatus
from app.models.support_ticket import SupportTicket
from app.repositories.entitlements import EntitlementRepository

router = APIRouter(prefix="/{slug}/dashboard/widgets", tags=["admin:dashboard-widgets"])


# ── Response models ──────────────────────────────────────────────────────────
# A discriminated set: the "type" literal lets the frontend dispatch generically
# on whatever comes back from a given widget key.

class StatWidgetOut(BaseModel):
    type: Literal["stat"] = "stat"
    label: str
    value: float
    unit: str | None = None


class SeriesPoint(BaseModel):
    label: str
    value: float


class ChartWidgetOut(BaseModel):
    type: Literal["chart"] = "chart"
    label: str
    series: list[SeriesPoint]


class BarWidgetOut(BaseModel):
    type: Literal["bar"] = "bar"
    label: str
    bars: list[SeriesPoint]


class DonutWidgetOut(BaseModel):
    type: Literal["donut"] = "donut"
    label: str
    slices: list[SeriesPoint]


class FunnelWidgetOut(BaseModel):
    type: Literal["funnel"] = "funnel"
    label: str
    stages: list[SeriesPoint]


class ListItemOut(BaseModel):
    title: str
    subtitle: str | None = None
    severity: Literal["info", "warning", "critical"] = "info"


class ListWidgetOut(BaseModel):
    type: Literal["list"] = "list"
    label: str
    items: list[ListItemOut]


class GaugeItemOut(BaseModel):
    label: str
    used: float
    limit: float | None


class GaugeWidgetOut(BaseModel):
    type: Literal["gauge"] = "gauge"
    label: str
    items: list[GaugeItemOut]


WidgetOut = (
    StatWidgetOut
    | ChartWidgetOut
    | BarWidgetOut
    | DonutWidgetOut
    | FunnelWidgetOut
    | ListWidgetOut
    | GaugeWidgetOut
)


# ── Date-range bucketing helpers ─────────────────────────────────────────────
# Shared by the "bucketed series" widgets (chart.revenue, chart.agent_runs_7d,
# chart.token_usage) when the caller supplies an explicit start/end range.
# Buckets by calendar day when the range is short enough to stay readable,
# otherwise falls back to Monday-aligned weeks.

_MAX_DAILY_BUCKETS = 60


def _resolve_bucket_starts(range_start: date, range_end: date) -> tuple[list[date], bool]:
    """Ordered bucket start-dates spanning ``[range_start, range_end]`` inclusive.

    Returns ``(starts, by_week)``. Buckets by day when the range is short
    enough (<= ``_MAX_DAILY_BUCKETS`` days); otherwise by Monday-aligned week.
    """
    span_days = (range_end - range_start).days + 1
    if span_days <= _MAX_DAILY_BUCKETS:
        return [range_start + timedelta(days=i) for i in range(span_days)], False

    first_week = range_start - timedelta(days=range_start.weekday())
    last_week = range_end - timedelta(days=range_end.weekday())
    starts: list[date] = []
    cur = first_week
    while cur <= last_week:
        starts.append(cur)
        cur += timedelta(weeks=1)
    return starts, True


def _bucket_label(bucket_start: date, by_week: bool) -> str:
    return f"Wk of {bucket_start.strftime('%b %d')}" if by_week else bucket_start.strftime("%b %d")


def _assign_bucket(day: date, by_week: bool) -> date:
    return day - timedelta(days=day.weekday()) if by_week else day


# ── Default widgets (7) ──────────────────────────────────────────────────────

async def _stat_products(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    value = await session.scalar(
        select(func.count()).where(
            Product.business_id == business.id,
            Product.status == ProductStatus.ACTIVE,
        )
    ) or 0
    return StatWidgetOut(label="Active Products", value=float(value))


async def _stat_customers(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    value = await session.scalar(
        select(func.count()).where(Customer.business_id == business.id)
    ) or 0
    return StatWidgetOut(label="Customers", value=float(value))


async def _stat_open_tickets(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    rows = (
        await session.execute(
            select(SupportTicket.status, func.count().label("n"))
            .where(SupportTicket.business_id == business.id)
            .group_by(SupportTicket.status)
        )
    ).all()
    by_status = {str(r.status): r.n for r in rows}
    total_open = sum(v for k, v in by_status.items() if k in {s.value for s in _OPEN_STATUSES})
    return StatWidgetOut(label="Open Tickets", value=float(total_open))


async def _stat_products_delivered(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    value = await session.scalar(
        select(func.count()).where(
            Fulfillment.business_id == business.id,
            Fulfillment.status == FulfillmentStatus.DELIVERED,
        )
    ) or 0
    return StatWidgetOut(label="Products Delivered", value=float(value))


async def _chart_revenue(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> ChartWidgetOut:
    """Default: weekly bucketing, last 8 weeks (the simple default for this
    per-widget endpoint — the three-way week/month/year toggle stays exclusive
    to the aggregated /metrics endpoint). When an explicit range is given,
    buckets that range instead (by day, or by week if it's long)."""
    day_col = cast(Payment.created_at, Date)
    conditions = [Payment.business_id == business.id, Payment.status == PaymentStatus.SUCCESS]
    if range_start is not None and range_end is not None:
        conditions += [Payment.created_at >= range_start, Payment.created_at < range_end]
    rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.coalesce(func.sum(Payment.amount), 0).label("amount"),
            )
            .where(*conditions)
            .group_by(day_col)
        )
    ).all()
    daily = [(r.day, Decimal(r.amount)) for r in rows]

    if range_start is not None and range_end is not None:
        start_date = range_start.date()
        end_date = (range_end - timedelta(days=1)).date()
        bucket_starts, by_week = _resolve_bucket_starts(start_date, end_date)
        sums = {b: Decimal("0") for b in bucket_starts}
        for day, amount in daily:
            key = _assign_bucket(day, by_week)
            if key in sums:
                sums[key] += amount
        series = [
            SeriesPoint(label=_bucket_label(b, by_week), value=float(sums[b])) for b in bucket_starts
        ]
        return ChartWidgetOut(label="Revenue", series=series)

    weeks = _bucket_by_week(daily)
    return ChartWidgetOut(
        label="Revenue",
        series=[SeriesPoint(label=w["label"], value=w["amount"]) for w in weeks],
    )


async def _list_needs_attention(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> ListWidgetOut:
    """Failed payments + urgent open tickets + stuck fulfillments, merged."""
    now = datetime.now(timezone.utc)
    stuck_cutoff = now - timedelta(hours=24)
    _SEVERITY_RANK = {"critical": 2, "warning": 1, "info": 0}

    failed_payments = (
        await session.execute(
            select(Payment)
            .where(Payment.business_id == business.id, Payment.status == PaymentStatus.FAILED)
            .order_by(Payment.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    urgent_tickets = (
        await session.execute(
            select(SupportTicket)
            .where(
                SupportTicket.business_id == business.id,
                SupportTicket.priority == TicketPriority.URGENT,
                SupportTicket.status.notin_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
            )
            .order_by(SupportTicket.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    stuck_fulfillments = (
        await session.execute(
            select(Fulfillment)
            .where(
                Fulfillment.business_id == business.id,
                Fulfillment.status == FulfillmentStatus.PENDING,
                Fulfillment.created_at < stuck_cutoff,
            )
            .order_by(Fulfillment.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    entries: list[tuple[str, ListItemOut]] = []
    for p in failed_payments:
        entries.append((
            "critical",
            ListItemOut(
                title=f"Payment failed — {p.currency} {p.amount}",
                subtitle=p.failure_reason,
                severity="critical",
            ),
        ))
    for t in urgent_tickets:
        entries.append((
            "warning",
            ListItemOut(
                title=t.summary or t.reason,
                subtitle=f"Ticket {t.reference}",
                severity="warning",
            ),
        ))
    for f in stuck_fulfillments:
        entries.append((
            "critical",
            ListItemOut(
                title="Fulfillment stuck for over 24h",
                subtitle=f"Fulfillment {f.id}",
                severity="critical",
            ),
        ))

    entries.sort(key=lambda e: _SEVERITY_RANK[e[0]], reverse=True)
    items = [item for _, item in entries[:10]]
    return ListWidgetOut(label="Needs Attention", items=items)


async def _gauge_plan_usage(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> GaugeWidgetOut:
    """Usage-vs-limit bars for this business's catalog modules.

    The item list is dynamic: only the catalog modules actually enabled for
    this business (per its resolved entitlements) get an item, so a
    Services-only business doesn't see a meaningless "Products: 0" bar.
    Knowledge Articles has no module gate today, so it always appears.
    """
    ent = await EntitlementRepository(session).get_or_create(business.id)
    resolved = resolve(ent.plan, ent.overrides)

    items: list[GaugeItemOut] = []

    if check(resolved, FeatureFlag.MODULE_PRODUCTS):
        products_used = await session.scalar(
            select(func.count()).where(
                Product.business_id == business.id,
                Product.status != ProductStatus.ARCHIVED,
            )
        ) or 0
        products_limit = get_limit(resolved, FeatureFlag.PRODUCTS_LIMIT)
        items.append(
            GaugeItemOut(
                label="Products",
                used=float(products_used),
                limit=float(products_limit) if products_limit is not None else None,
            )
        )

    if check(resolved, FeatureFlag.MODULE_SERVICES):
        services_used = await session.scalar(
            select(func.count()).where(
                Service.business_id == business.id,
                Service.status != ServiceStatus.ARCHIVED,
            )
        ) or 0
        items.append(
            GaugeItemOut(label="Services", used=float(services_used), limit=None)
        )

    if check(resolved, FeatureFlag.MODULE_OFFERS):
        offers_used = await session.scalar(
            select(func.count()).where(
                Offer.business_id == business.id,
                Offer.status == OfferStatus.ACTIVE,
            )
        ) or 0
        items.append(
            GaugeItemOut(label="Offers", used=float(offers_used), limit=None)
        )

    if check(resolved, FeatureFlag.MODULE_COUPONS):
        coupons_used = await session.scalar(
            select(func.count()).where(
                Coupon.business_id == business.id,
                Coupon.status == CouponStatus.ACTIVE,
            )
        ) or 0
        items.append(
            GaugeItemOut(label="Coupons", used=float(coupons_used), limit=None)
        )

    knowledge_used = await session.scalar(
        select(func.count()).where(Knowledge.business_id == business.id)
    ) or 0
    knowledge_limit = get_limit(resolved, FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT)
    items.append(
        GaugeItemOut(
            label="Knowledge Articles",
            used=float(knowledge_used),
            limit=float(knowledge_limit) if knowledge_limit is not None else None,
        )
    )

    return GaugeWidgetOut(label="Plan Usage", items=items)


# ── Gated: existing "advanced" widgets, individually gated ──────────────────

async def _stat_active_sessions(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    value = await session.scalar(
        select(func.count()).where(
            Conversation.business_id == business.id,
            Conversation.status == ConversationStatus.ACTIVE,
            Conversation.channel == Channel.WEB,
        )
    ) or 0
    return StatWidgetOut(label="Active Sessions", value=float(value))


async def _stat_agent_runs_today(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    conditions = [AgentRun.business_id == business.id]
    if range_start is not None and range_end is not None:
        conditions += [AgentRun.created_at >= range_start, AgentRun.created_at < range_end]
        label = "Agent Runs"
    else:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        conditions.append(AgentRun.created_at >= today_start)
        label = "Agent Runs Today"
    value = await session.scalar(select(func.count()).where(*conditions)) or 0
    return StatWidgetOut(label=label, value=float(value))


async def _stat_published_articles(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    value = await session.scalar(
        select(func.count()).where(
            Knowledge.business_id == business.id,
            Knowledge.status == KnowledgeStatus.PUBLISHED,
        )
    ) or 0
    return StatWidgetOut(label="Published Articles", value=float(value))


async def _day_map_range(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> dict[str, dict[str, Any]]:
    """Shared agent-run bucketing for chart.agent_runs_7d / chart.token_usage.

    Defaults to the last 7 days, bucketed by day, with no upper bound (i.e.
    through "now") — the existing behavior. When an explicit range is given,
    buckets that range instead (by day, or by week if it's long), bounded on
    both ends.
    """
    query_end: datetime | None
    if range_start is not None and range_end is not None:
        query_start = range_start
        query_end = range_end
        start_date = range_start.date()
        end_date = (range_end - timedelta(days=1)).date()
    else:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query_start = today_start - timedelta(days=6)
        query_end = None
        start_date = query_start.date()
        end_date = today_start.date()

    bucket_starts, by_week = _resolve_bucket_starts(start_date, end_date)

    day_col = cast(AgentRun.created_at, Date)
    conditions = [AgentRun.business_id == business.id, AgentRun.created_at >= query_start]
    if query_end is not None:
        conditions.append(AgentRun.created_at < query_end)
    rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.count().label("count"),
                func.coalesce(
                    func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0
                ).label("tokens"),
            )
            .where(*conditions)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    bucket_map: dict[str, dict[str, Any]] = {
        b.isoformat(): {"date": _bucket_label(b, by_week), "runs": 0, "tokens": 0}
        for b in bucket_starts
    }
    for r in rows:
        key = _assign_bucket(r.day, by_week).isoformat()
        if key in bucket_map:
            bucket_map[key]["runs"] += int(r.count)
            bucket_map[key]["tokens"] += int(r.tokens)
    return bucket_map


async def _chart_agent_runs_7d(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> ChartWidgetOut:
    day_map = await _day_map_range(business, session, range_start, range_end)
    label = "Agent Runs" if range_start is not None else "Agent Runs (7-day)"
    return ChartWidgetOut(
        label=label,
        series=[SeriesPoint(label=d["date"], value=d["runs"]) for d in day_map.values()],
    )


async def _chart_token_usage(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> ChartWidgetOut:
    day_map = await _day_map_range(business, session, range_start, range_end)
    label = "AI Token Usage" if range_start is not None else "AI Token Usage (7-day)"
    return ChartWidgetOut(
        label=label,
        series=[SeriesPoint(label=d["date"], value=d["tokens"]) for d in day_map.values()],
    )


async def _chart_ticket_status(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> DonutWidgetOut:
    rows = (
        await session.execute(
            select(SupportTicket.status, func.count().label("n"))
            .where(SupportTicket.business_id == business.id)
            .group_by(SupportTicket.status)
        )
    ).all()
    return DonutWidgetOut(
        label="Ticket Status Breakdown",
        slices=[SeriesPoint(label=str(r.status), value=r.n) for r in rows],
    )


async def _chart_product_catalog(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> BarWidgetOut:
    rows = (
        await session.execute(
            select(Product.status, func.count().label("n"))
            .where(Product.business_id == business.id)
            .group_by(Product.status)
        )
    ).all()
    return BarWidgetOut(
        label="Product Catalog Breakdown",
        bars=[SeriesPoint(label=str(r.status), value=r.n) for r in rows],
    )


async def _chart_ticket_priority(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> BarWidgetOut:
    rows = (
        await session.execute(
            select(SupportTicket.priority, func.count().label("n"))
            .where(
                SupportTicket.business_id == business.id,
                SupportTicket.status.notin_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
            )
            .group_by(SupportTicket.priority)
        )
    ).all()
    return BarWidgetOut(
        label="Open Ticket Priority",
        bars=[SeriesPoint(label=str(r.priority), value=r.n) for r in rows],
    )


# ── Gated: brand-new widgets ─────────────────────────────────────────────────

async def _stat_orders_today(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    conditions = [Order.business_id == business.id]
    if range_start is not None and range_end is not None:
        conditions += [Order.created_at >= range_start, Order.created_at < range_end]
        label = "Orders"
    else:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        conditions.append(Order.created_at >= today_start)
        label = "Orders Today"
    value = await session.scalar(select(func.count()).where(*conditions)) or 0
    return StatWidgetOut(label=label, value=float(value))


async def _donut_order_status(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> DonutWidgetOut:
    """All-time by default (existing behavior); scoped to the range when given."""
    conditions = [Order.business_id == business.id]
    if range_start is not None and range_end is not None:
        conditions += [Order.created_at >= range_start, Order.created_at < range_end]
    rows = (
        await session.execute(
            select(Order.status, func.count().label("n")).where(*conditions).group_by(Order.status)
        )
    ).all()
    return DonutWidgetOut(
        label="Order Status Breakdown",
        slices=[SeriesPoint(label=str(r.status), value=r.n) for r in rows],
    )


async def _bar_top_products(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> BarWidgetOut:
    """All-time by default (existing behavior); scoped to the range when given."""
    conditions = [Order.business_id == business.id]
    if range_start is not None and range_end is not None:
        conditions += [Order.created_at >= range_start, Order.created_at < range_end]
    rows = (
        await session.execute(
            select(
                OrderItem.product_name,
                func.sum(OrderItem.quantity).label("total_qty"),
            )
            .join(Order, OrderItem.order_id == Order.id)
            .where(*conditions)
            .group_by(OrderItem.product_name)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(5)
        )
    ).all()
    return BarWidgetOut(
        label="Top Selling Products",
        bars=[SeriesPoint(label=r.product_name, value=int(r.total_qty)) for r in rows],
    )


async def _funnel_sales(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> FunnelWidgetOut:
    """All-time by default (existing behavior); scoped to the range when given.

    Each stage is filtered by its own timestamp column (Order.created_at for
    the first stage, Payment.created_at for the other two) — matching the
    original's independent per-stage counts rather than joining them into a
    single per-order cohort.
    """
    order_conditions = [Order.business_id == business.id]
    payment_conditions = [Payment.business_id == business.id]
    if range_start is not None and range_end is not None:
        order_conditions += [Order.created_at >= range_start, Order.created_at < range_end]
        payment_conditions += [Payment.created_at >= range_start, Payment.created_at < range_end]

    orders_created = await session.scalar(select(func.count()).where(*order_conditions)) or 0
    payment_initiated = await session.scalar(
        select(func.count(func.distinct(Payment.order_id))).where(*payment_conditions)
    ) or 0
    payment_successful = await session.scalar(
        select(func.count(func.distinct(Payment.order_id))).where(
            *payment_conditions, Payment.status == PaymentStatus.SUCCESS
        )
    ) or 0
    return FunnelWidgetOut(
        label="Sales Funnel",
        stages=[
            SeriesPoint(label="Orders Created", value=orders_created),
            SeriesPoint(label="Payment Initiated", value=payment_initiated),
            SeriesPoint(label="Payment Successful", value=payment_successful),
        ],
    )


async def _stat_active_coupons(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    value = await session.scalar(
        select(func.count()).where(
            Coupon.business_id == business.id,
            Coupon.status == CouponStatus.ACTIVE,
        )
    ) or 0
    return StatWidgetOut(label="Active Coupons", value=float(value))


async def _stat_active_offers(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    value = await session.scalar(
        select(func.count()).where(
            Offer.business_id == business.id,
            Offer.status == OfferStatus.ACTIVE,
        )
    ) or 0
    return StatWidgetOut(label="Active Offers", value=float(value))


async def _stat_appointments_upcoming(
    business: Business, session: AsyncSession, range_start: datetime | None, range_end: datetime | None
) -> StatWidgetOut:
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=7)
    value = await session.scalar(
        select(func.count()).where(
            Appointment.business_id == business.id,
            Appointment.scheduled_at >= now,
            Appointment.scheduled_at <= window_end,
            Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED]),
        )
    ) or 0
    return StatWidgetOut(label="Upcoming Appointments", value=float(value))


# ── Registry ──────────────────────────────────────────────────────────────────

WIDGET_HANDLERS: dict[
    str,
    Callable[[Business, AsyncSession, datetime | None, datetime | None], Awaitable[BaseModel]],
] = {
    # defaults
    "stat.products": _stat_products,
    "stat.customers": _stat_customers,
    "stat.open_tickets": _stat_open_tickets,
    "stat.products_delivered": _stat_products_delivered,
    "chart.revenue": _chart_revenue,
    "list.needs_attention": _list_needs_attention,
    "gauge.plan_usage": _gauge_plan_usage,
    # gated — existing advanced
    "stat.active_sessions": _stat_active_sessions,
    "stat.agent_runs_today": _stat_agent_runs_today,
    "stat.published_articles": _stat_published_articles,
    "chart.agent_runs_7d": _chart_agent_runs_7d,
    "chart.ticket_status": _chart_ticket_status,
    "chart.product_catalog": _chart_product_catalog,
    "chart.token_usage": _chart_token_usage,
    "chart.ticket_priority": _chart_ticket_priority,
    # gated — new
    "stat.orders_today": _stat_orders_today,
    "donut.order_status": _donut_order_status,
    "bar.top_products": _bar_top_products,
    "funnel.sales": _funnel_sales,
    "stat.active_coupons": _stat_active_coupons,
    "stat.active_offers": _stat_active_offers,
    "stat.appointments_upcoming": _stat_appointments_upcoming,
}


@router.get("/{widget_key}", response_model=WidgetOut)
async def get_widget_data(
    slug: str,
    widget_key: str,
    start: str | None = None,
    end: str | None = None,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> BaseModel:
    """Fetch data for a single dashboard widget, gated per-business.

    Access is resolved from this business's entitlements (plan + overrides)
    via ``widget_allowed`` — the same source of truth used by the widget
    *selector* endpoints in ``app/api/admin/dashboard.py``.

    ``start``/``end`` (``YYYY-MM-DD``, inclusive) are optional. When both are
    absent, every handler falls back to its own existing hardcoded default
    window (unchanged from before this param existed). When either is given,
    the resolved ``[range_start, range_end)`` window is passed to every
    handler uniformly — period/activity-based widgets respect it; purely
    snapshot/current-state widgets (e.g. a live count of active products)
    ignore it, since a date range doesn't meaningfully apply to "right now".
    """
    handler = WIDGET_HANDLERS.get(widget_key)
    if handler is None:
        raise NotFoundError(f"Unknown widget key: {widget_key}", details={"widget_key": widget_key})

    range_start: datetime | None = None
    range_end: datetime | None = None
    if start is not None or end is not None:
        range_start, range_end = parse_date_range(
            start,
            end,
            default_start=lambda: datetime.min.replace(tzinfo=timezone.utc),
            default_end=lambda: datetime.now(timezone.utc),
        )

    ent = await EntitlementRepository(session).get_or_create(business.id)
    resolved = resolve(ent.plan, ent.overrides)
    if not widget_allowed(widget_key, resolved):
        raise ForbiddenError(f"The '{widget_key}' widget is not enabled for this business.")

    return await handler(business, session, range_start, range_end)
