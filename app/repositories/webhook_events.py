"""Webhook event log - the idempotency gate.

Not tenant-scoped: the row is written before the payload has been resolved to
a business, which is the whole point (an unparseable payload is still
recorded).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import WebhookSource, WebhookStatus
from app.models.webhook_event import WebhookEvent


class WebhookEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_if_new(
        self,
        *,
        source: WebhookSource,
        external_event_id: str,
        payload: dict[str, Any],
        signature_verified: bool = True,
        business_id: uuid.UUID | None = None,
    ) -> WebhookEvent | None:
        """Insert the event, or return ``None`` if it is a redelivery.

        ``ON CONFLICT DO NOTHING`` rather than a SELECT-then-INSERT: two
        concurrent redeliveries of the same event would both see "not present"
        and both proceed. Letting the unique index arbitrate means exactly one
        caller gets a row back and the other gets ``None``, with no race.

        A ``None`` return is the caller's signal to answer 200 and stop.
        """
        stmt = (
            pg_insert(WebhookEvent)
            .values(
                source=source,
                external_event_id=external_event_id,
                payload=payload,
                signature_verified=signature_verified,
                business_id=business_id,
                status=WebhookStatus.RECEIVED,
            )
            .on_conflict_do_nothing(
                constraint="uq_webhook_events_source_external_event_id"
            )
            .returning(WebhookEvent)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, event_id: uuid.UUID) -> WebhookEvent | None:
        stmt = select(WebhookEvent).where(WebhookEvent.id == event_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def mark_processing(self, event: WebhookEvent) -> None:
        event.status = WebhookStatus.PROCESSING
        event.attempts += 1
        await self.session.flush()

    async def mark_processed(
        self, event: WebhookEvent, *, business_id: uuid.UUID | None = None
    ) -> None:
        event.status = WebhookStatus.PROCESSED
        event.processed_at = datetime.now(timezone.utc)
        event.error = None
        if business_id is not None:
            event.business_id = business_id
        await self.session.flush()

    async def mark_failed(self, event: WebhookEvent, error: str) -> None:
        event.status = WebhookStatus.FAILED
        event.processed_at = datetime.now(timezone.utc)
        # Truncated: a full provider traceback can be enormous, and this column
        # is for triage, not forensics - `payload` holds the replayable truth.
        event.error = error[:2000]
        await self.session.flush()

    async def mark_ignored(self, event: WebhookEvent, reason: str) -> None:
        """Recognised but irrelevant, e.g. a WhatsApp delivery-status callback."""
        event.status = WebhookStatus.IGNORED
        event.processed_at = datetime.now(timezone.utc)
        event.error = reason[:2000]
        await self.session.flush()

    async def list_failed(self, *, limit: int = 100) -> Sequence[WebhookEvent]:
        """Replay queue."""
        stmt = (
            select(WebhookEvent)
            .where(WebhookEvent.status == WebhookStatus.FAILED)
            .order_by(WebhookEvent.created_at)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
