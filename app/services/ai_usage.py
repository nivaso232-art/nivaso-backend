"""AI usage cost estimation and monthly usage-limit enforcement.

Two jobs live here, deliberately kept together:

1. ``estimate_cost`` — the one true cost formula, previously duplicated as
   ``_estimate_cost`` in ``app/api/admin/metrics.py``. Both the per-business
   dashboard widget and the super-admin usage view (and the limit check
   below) now share this single implementation.
2. ``check_and_notify_usage_limit`` — called by all three agent runners
   (Claude/Gemini/Groq) right after a new ``AgentRun`` row is persisted. If
   the business has a super-admin-set monthly spend cap
   (``FeatureFlag.AI_USAGE_LIMIT_USD``) and has crossed it, this fires one
   in-app ``Notification`` per calendar month (deduped via
   ``NotificationRepository.exists_since``) rather than one per agent turn.

This module must never raise into the agent turn it is called from - a
usage-tracking side effect breaking a customer-facing reply would be a much
worse outcome than a missed notification. See ``check_and_notify_usage_limit``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import get_limit
from app.models.agent_run import USD_PER_MTOK_INPUT, USD_PER_MTOK_OUTPUT, AgentRun
from app.models.notification import Notification, NotificationSeverity
from app.repositories.entitlements import EntitlementRepository
from app.repositories.notifications import NotificationRepository

log = structlog.get_logger(__name__)

# Machine-readable notification type for the usage-limit alert. Also the key
# used to dedupe: at most one of these is created per business per month.
NOTIFICATION_TYPE_USAGE_LIMIT = "ai_usage_limit_reached"


def estimate_cost(input_tok: int, output_tok: int, cache_read: int, cache_creation: int) -> float:
    """Rough cost estimate in USD. Cached reads bill at ~0.1x input.

    Exact same formula as ``AgentRun.estimated_usd`` / the former
    ``metrics.py::_estimate_cost`` - kept here as the one shared implementation.
    """
    billed = input_tok + cache_creation * 1.25 + cache_read * 0.1
    return round(
        billed / 1_000_000 * USD_PER_MTOK_INPUT
        + output_tok / 1_000_000 * USD_PER_MTOK_OUTPUT,
        4,
    )


def month_start_utc(today: date | None = None) -> datetime:
    """Midnight UTC on the 1st of ``today``'s month (default: current UTC month).

    Public (not just an ``ai_usage`` internal) because the super-admin usage
    endpoint reuses it for its daily-breakdown window.
    """
    d = today or datetime.now(timezone.utc).date()
    return datetime(d.year, d.month, 1, tzinfo=timezone.utc)


async def get_monthly_usage(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    month_start: date | None = None,
) -> dict:
    """Sum AgentRun tokens/cost for ``business_id`` from ``month_start`` to now.

    ``month_start`` defaults to the 1st of the current UTC month.
    """
    start = month_start_utc(month_start)

    row = (
        await session.execute(
            select(
                func.count().label("runs"),
                func.coalesce(func.sum(AgentRun.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(AgentRun.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(AgentRun.cache_read_tokens), 0).label("cache_read"),
                func.coalesce(func.sum(AgentRun.cache_creation_tokens), 0).label("cache_creation"),
            ).where(
                AgentRun.business_id == business_id,
                AgentRun.created_at >= start,
            )
        )
    ).one()

    input_tokens = int(row.input_tokens)
    output_tokens = int(row.output_tokens)
    return {
        "runs": int(row.runs),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": estimate_cost(
            input_tokens, output_tokens, int(row.cache_read), int(row.cache_creation)
        ),
    }


async def check_and_notify_usage_limit(session: AsyncSession, business_id: uuid.UUID) -> None:
    """Fire an in-app notification once a business crosses its monthly AI spend cap.

    Call this right after persisting a new ``AgentRun`` row. Never raises -
    any failure here must not break the agent response path, so the whole
    body is wrapped in try/except and logged, matching the defensive
    try/except-swallow pattern used for non-critical side effects elsewhere
    in this codebase (see ``_write_audit`` in
    ``app/api/super_admin/businesses.py``).
    """
    try:
        entitlements = await EntitlementRepository(session).resolved(business_id)
        limit = get_limit(entitlements, FeatureFlag.AI_USAGE_LIMIT_USD)
        if limit is None:
            return  # no cap set - purely a super-admin opt-in feature

        usage = await get_monthly_usage(session, business_id)
        if usage["cost_usd"] < limit:
            return

        month_start = month_start_utc()
        notif_repo = NotificationRepository(session, business_id)
        if await notif_repo.exists_since(NOTIFICATION_TYPE_USAGE_LIMIT, month_start):
            return  # already notified this business this month - don't spam

        async with UnitOfWork(session):
            await notif_repo.add(
                Notification(
                    type=NOTIFICATION_TYPE_USAGE_LIMIT,
                    title="AI usage limit reached",
                    message=(
                        f"Your AI usage this month (${usage['cost_usd']:.2f}) has "
                        f"reached your limit of ${limit:.2f}."
                    ),
                    severity=NotificationSeverity.WARNING,
                )
            )
        log.info(
            "ai_usage_limit_notification_created",
            business_id=str(business_id),
            cost_usd=usage["cost_usd"],
            limit_usd=limit,
        )
    except Exception:
        log.exception("ai_usage_limit_check_failed", business_id=str(business_id))
