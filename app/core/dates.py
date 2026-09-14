"""Shared date-range query-param parsing.

Multiple admin endpoints (``app/api/admin/ai_usage.py``,
``app/api/admin/dashboard_widgets.py``) accept optional ``start``/``end``
query params as ``YYYY-MM-DD`` strings (inclusive of the calendar day
supplied) and need to turn them into a UTC ``[start, end)`` half-open
datetime window for SQL comparisons. This is the one shared implementation —
callers supply their own defaults for whichever side is left unspecified, so
each endpoint can keep its own "what if the caller doesn't pass a range"
behavior without duplicating the parsing/validation logic.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from app.core.errors import ValidationError


def parse_date_range(
    start: str | None,
    end: str | None,
    *,
    default_start: Callable[[], datetime],
    default_end: Callable[[], datetime],
) -> tuple[datetime, datetime]:
    """Parse optional ``YYYY-MM-DD`` strings into a UTC ``[start, end)`` window.

    ``start``/``end`` are each independently optional: whichever is absent
    falls back to calling the corresponding ``default_*`` callable (deferred
    so "now" is evaluated at call time, not import time). ``end`` is inclusive
    of the calendar day supplied — internally converted to an exclusive upper
    bound at the start of the following day.

    Raises ``ValidationError`` on a malformed date string or when the
    resolved window is empty/inverted (``start >= end``).
    """
    try:
        start_dt = (
            datetime.combine(date.fromisoformat(start), datetime.min.time(), tzinfo=timezone.utc)
            if start
            else default_start()
        )
        end_dt = (
            datetime.combine(date.fromisoformat(end), datetime.min.time(), tzinfo=timezone.utc)
            + timedelta(days=1)
            if end
            else default_end()
        )
    except ValueError:
        raise ValidationError("start/end must be ISO dates (YYYY-MM-DD).")
    if start_dt >= end_dt:
        raise ValidationError("start must be before end.")
    return start_dt, end_dt
