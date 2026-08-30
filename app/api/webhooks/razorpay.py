"""Razorpay webhook handler.

``POST /webhooks/razorpay``
    Payment confirmation callbacks. Flow:
      1. Verify HMAC-SHA256 (X-Razorpay-Signature).
      2. Return 200 immediately — Razorpay retries on non-2xx.
      3. Record a ``webhook_event`` row for idempotency.
      4. Parse the event into a ``ProviderOutcome``.
      5. Global-lookup the payment to find the tenant (``business_id``).
      6. Call ``PaymentService.apply_provider_outcome()`` with the correct tenant.
      7. If the result needs escalation (double charge, amount mismatch),
         open a support ticket automatically.
      8. Mark the event processed.

This is the ONLY place in the codebase that transitions an order to PAID.
``PaymentRepository.get_by_provider_payment_id`` and
``get_by_provider_link_id`` are deliberately not tenant-scoped so the tenant
can be established from the payment row itself.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.errors import NotFoundError, SignatureError
from app.core.logging import bind_request_context, clear_request_context
from app.core.security import verify_razorpay_signature
from app.core.uow import UnitOfWork
from app.models.enums import PaymentProvider, WebhookSource
from app.models.payment import Payment
from app.providers.razorpay.client import parse_webhook_outcome
from app.repositories.payments import PaymentRepository
from app.repositories.webhook_events import WebhookEventRepository
from app.services.payment_service import PaymentService, ProviderOutcome
from app.services.support_service import SupportService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/razorpay", tags=["webhooks"])


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_razorpay_signature: str | None = Header(default=None),
) -> Response:
    """Inbound Razorpay payment events."""
    raw_body = await request.body()

    try:
        verify_razorpay_signature(
            webhook_secret=settings.razorpay_webhook_secret,
            payload=raw_body,
            header=x_razorpay_signature,
        )
    except SignatureError as exc:
        log.warning("razorpay_signature_invalid", error=str(exc))
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    payload = await request.json()
    background_tasks.add_task(_process_razorpay_event, payload)
    return Response(status_code=status.HTTP_200_OK)


async def _process_razorpay_event(payload: dict) -> None:
    event_id = payload.get("id", "")
    event_type = payload.get("event", "")
    bind_request_context(source="razorpay", event=event_type, event_id=event_id)

    async with SessionFactory() as session:
        webhook_repo = WebhookEventRepository(session)

        async with UnitOfWork(session):
            event = await webhook_repo.record_if_new(
                source=WebhookSource.RAZORPAY,
                external_event_id=event_id,
                payload=payload,
                signature_verified=True,
            )
            if event is None:
                log.info("razorpay_webhook_duplicate", event_id=event_id)
                return
            await webhook_repo.mark_processing(event)

        outcome = parse_webhook_outcome(payload)
        if outcome is None:
            log.info("razorpay_event_ignored", event=event_type)
            async with UnitOfWork(session):
                await webhook_repo.mark_ignored(
                    event, f"unhandled event type: {event_type}"
                )
            clear_request_context()
            return

        try:
            async with UnitOfWork(session):
                # Global lookup to establish tenant. ``get_by_provider_*`` methods
                # are deliberately not scoped by business_id — see payments.py.
                payment = await _locate_payment_globally(session, outcome)
                if payment is None:
                    raise NotFoundError(
                        "No payment attempt matches this Razorpay event.",
                        details={
                            "provider_payment_id": outcome.provider_payment_id,
                            "provider_payment_link_id": outcome.provider_payment_link_id,
                        },
                    )

                business_id = payment.business_id
                payment_svc = PaymentService(session, business_id)
                result = await payment_svc.apply_provider_outcome(outcome)

                escalation = PaymentService.escalation_for(result)
                if escalation is not None:
                    reason, priority, summary = escalation
                    support_svc = SupportService(session, business_id)
                    await support_svc.create_ticket(
                        customer_id=result.order.customer_id,
                        reason=reason,
                        summary=summary,
                        priority=priority,
                        reuse_open=False,
                    )

                await webhook_repo.mark_processed(event, business_id=business_id)

            log.info(
                "razorpay_payment_processed",
                order_ref=result.order.reference,
                order_status=result.order.status.value,
                is_duplicate=result.is_duplicate,
                needs_escalation=result.needs_escalation,
            )

        except NotFoundError as exc:
            log.error("razorpay_payment_not_found", error=str(exc), event_id=event_id)
            async with UnitOfWork(session):
                await webhook_repo.mark_failed(event, str(exc))
        except Exception as exc:
            log.exception("razorpay_processing_failed", error=str(exc))
            async with UnitOfWork(session):
                await webhook_repo.mark_failed(event, str(exc))

    clear_request_context()


async def _locate_payment_globally(
    session: AsyncSession,
    outcome: ProviderOutcome,
) -> Payment | None:
    """Find the payment row without tenant filtering to establish business_id.

    Both lookup methods in PaymentRepository are cross-tenant by design —
    see the comment on ``get_by_provider_payment_id``. We pass a dummy UUID
    as the business_id because these methods never use it.
    """
    import uuid
    _dummy = uuid.UUID("00000000-0000-0000-0000-000000000000")
    repo = PaymentRepository(session, _dummy)

    payment = await repo.get_by_provider_payment_id(
        provider=outcome.provider,
        provider_payment_id=outcome.provider_payment_id,
    )
    if payment is not None:
        return payment

    if outcome.provider_payment_link_id:
        return await repo.get_by_provider_link_id(
            provider=outcome.provider,
            link_id=outcome.provider_payment_link_id,
        )

    return None
