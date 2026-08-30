"""Payment lifecycle.

This module enforces **rules 2, 6, and 7** - the ones that protect money.

Rule 2: nothing here marks a payment successful except
:meth:`PaymentService.apply_provider_outcome`, and its only caller is the
Razorpay webhook handler. A customer typing "bro I paid" reaches the agent,
the agent has no tool that leads here, and so the claim changes nothing. The
only thing that moves money-state is a signed webhook from the provider.

Rule 6: attempts are append-only. ``create_attempt`` inserts; nothing mutates a
FAILED row back into PENDING.

Rule 7: a second SUCCESS on an already-paid order is recorded truthfully,
flagged for refund, and escalated - not swallowed, and not allowed to look
like the payment that paid the order.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.enums import (
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    TicketPriority,
)
from app.models.order import Order
from app.models.payment import Payment
from app.repositories.orders import OrderRepository
from app.repositories.payments import PaymentRepository
from app.services.state_machine import (
    assert_order_transition,
    assert_payment_transition,
)

log = structlog.get_logger(__name__)

DOUBLE_PAYMENT_REASON = "DOUBLE_PAYMENT"


@dataclass(frozen=True)
class ProviderOutcome:
    """A normalised payment result parsed from a verified provider webhook.

    Channel-specific payload shapes are flattened in
    ``app/providers/razorpay/client.py`` so this service never has to know what
    Razorpay's JSON looks like.
    """

    provider: PaymentProvider
    provider_payment_id: str
    status: PaymentStatus
    amount: Decimal | None = None
    currency: str | None = None
    failure_reason: str | None = None
    provider_order_id: str | None = None
    provider_payment_link_id: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class OutcomeResult:
    """What ``apply_provider_outcome`` did, so the caller can log/escalate."""

    payment: Payment
    order: Order
    order_status_changed: bool
    is_duplicate: bool
    needs_escalation: bool


class PaymentService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.payments = PaymentRepository(session, business_id)
        self.orders = OrderRepository(session, business_id)

    # -- attempt creation (agent-reachable) --------------------------------

    async def create_attempt(
        self,
        *,
        order: Order,
        provider: PaymentProvider = PaymentProvider.RAZORPAY,
        provider_order_id: str | None = None,
        provider_payment_link_id: str | None = None,
        payment_url: str | None = None,
        reuse_open: bool = True,
    ) -> Payment:
        """Open a new payment attempt for an order.

        This is the *only* payment operation an agent tool can reach, and all
        it can do is create a PENDING row - it cannot set a status, an amount,
        or a provider id. The amount comes from ``order.total``, which itself
        came from the products table.

        Args:
            reuse_open: Return the existing open attempt instead of creating a
                second one. A customer tapping "pay" three times in ten
                seconds should get the same link back, not three payment rows.
        """
        if order.is_paid:
            raise ConflictError(
                "This order is already paid.",
                details={"reference": order.reference, "status": order.status.value},
            )
        if order.status is OrderStatus.CANCELLED:
            raise ConflictError(
                "This order was cancelled and cannot be paid.",
                details={"reference": order.reference},
            )

        if reuse_open:
            existing = await self.payments.get_open_for_order(order.id)
            if existing is not None:
                log.info(
                    "payment_attempt_reused",
                    payment_id=str(existing.id),
                    reference=order.reference,
                )
                return existing

        payment = Payment(
            order_id=order.id,
            provider=provider,
            provider_order_id=provider_order_id,
            provider_payment_link_id=provider_payment_link_id,
            payment_url=payment_url,
            amount=order.total,
            currency=order.currency,
            status=PaymentStatus.PENDING,
        )
        await self.payments.add(payment)

        log.info(
            "payment_attempt_created",
            payment_id=str(payment.id),
            reference=order.reference,
            amount=str(payment.amount),
        )
        return payment

    # -- provider outcome (webhook-only) -----------------------------------

    async def apply_provider_outcome(self, outcome: ProviderOutcome) -> OutcomeResult:
        """Apply a verified provider result to the payment and its order.

        **Only the Razorpay webhook route may call this.** It is the single
        point at which ``orders.status`` becomes PAID, by design.

        The payment row is located by, in order: the provider payment id (an
        already-recorded attempt, i.e. a redelivery), then the payment-link id
        (the PENDING row we created when issuing the link). A link-based flow
        has no payment id until money actually moves, so the second path is the
        normal one for a first-time success.
        """
        payment = await self._locate_payment(outcome)

        # Redelivery of an outcome we already applied. Return the current state
        # rather than re-running the transition (rule 9).
        if payment.status == outcome.status and payment.provider_payment_id:
            order = await self.orders.get_or_raise(payment.order_id)
            log.info(
                "payment_outcome_replayed",
                payment_id=str(payment.id),
                status=payment.status.value,
            )
            return OutcomeResult(
                payment=payment,
                order=order,
                order_status_changed=False,
                is_duplicate=payment.is_duplicate,
                needs_escalation=False,
            )

        order = await self.orders.get_or_raise(payment.order_id)

        assert_payment_transition(payment.status, outcome.status)

        payment.provider_payment_id = (
            outcome.provider_payment_id or payment.provider_payment_id
        )
        payment.provider_order_id = (
            outcome.provider_order_id or payment.provider_order_id
        )
        payment.status = outcome.status
        payment.failure_reason = outcome.failure_reason
        if outcome.raw_payload is not None:
            payment.raw_payload = outcome.raw_payload

        if outcome.status is PaymentStatus.SUCCESS:
            return await self._apply_success(payment, order, outcome)
        if outcome.status in (PaymentStatus.FAILED, PaymentStatus.CANCELLED):
            return await self._apply_failure(payment, order)

        await self.session.flush()
        return OutcomeResult(
            payment=payment,
            order=order,
            order_status_changed=False,
            is_duplicate=False,
            needs_escalation=False,
        )

    async def _apply_success(
        self, payment: Payment, order: Order, outcome: ProviderOutcome
    ) -> OutcomeResult:
        """Rule 7 lives here.

        If the order is already paid by a *different* attempt, this success is
        a double charge. It is recorded as SUCCESS - because it truly did
        succeed, and pretending otherwise would misstate what the customer was
        charged - but flagged ``is_duplicate`` / ``needs_refund`` so it is
        excluded from "which payment paid this order?" and shows up in the
        refund queue.
        """
        prior = await self.payments.get_successful_for_order(order.id)
        is_duplicate = prior is not None and prior.id != payment.id

        if is_duplicate:
            payment.is_duplicate = True
            payment.needs_refund = True
            await self.session.flush()

            log.warning(
                "duplicate_payment_detected",
                payment_id=str(payment.id),
                prior_payment_id=str(prior.id) if prior else None,
                reference=order.reference,
                amount=str(payment.amount),
            )
            # Order status untouched - it is already PAID and stays that way.
            return OutcomeResult(
                payment=payment,
                order=order,
                order_status_changed=False,
                is_duplicate=True,
                needs_escalation=True,
            )

        # Amount mismatch: the provider settled a different figure than we
        # asked for. Do not fail the payment (the money is real), but flag it -
        # a short-payment must not silently mark an order fully paid.
        amount_mismatch = (
            outcome.amount is not None and outcome.amount != payment.amount
        )
        if amount_mismatch:
            payment.needs_refund = True
            log.warning(
                "payment_amount_mismatch",
                payment_id=str(payment.id),
                expected=str(payment.amount),
                received=str(outcome.amount),
            )

        status_changed = order.status is not OrderStatus.PAID
        if status_changed:
            assert_order_transition(order.status, OrderStatus.PAID)
            order.status = OrderStatus.PAID

        await self.session.flush()

        log.info(
            "payment_succeeded",
            payment_id=str(payment.id),
            reference=order.reference,
            order_status=order.status.value,
        )
        return OutcomeResult(
            payment=payment,
            order=order,
            order_status_changed=status_changed,
            is_duplicate=False,
            needs_escalation=amount_mismatch,
        )

    async def _apply_failure(self, payment: Payment, order: Order) -> OutcomeResult:
        """Edge case 18. The failed attempt stays failed forever; a retry
        creates a new row."""
        status_changed = False
        if order.status is OrderStatus.PAYMENT_PENDING:
            assert_order_transition(order.status, OrderStatus.PAYMENT_FAILED)
            order.status = OrderStatus.PAYMENT_FAILED
            status_changed = True

        await self.session.flush()

        log.info(
            "payment_failed",
            payment_id=str(payment.id),
            reference=order.reference,
            reason=payment.failure_reason,
        )
        return OutcomeResult(
            payment=payment,
            order=order,
            order_status_changed=status_changed,
            is_duplicate=False,
            needs_escalation=False,
        )

    async def _locate_payment(self, outcome: ProviderOutcome) -> Payment:
        by_payment_id = await self.payments.get_by_provider_payment_id(
            provider=outcome.provider,
            provider_payment_id=outcome.provider_payment_id,
        )
        if by_payment_id is not None:
            return by_payment_id

        if outcome.provider_payment_link_id:
            by_link = await self.payments.get_by_provider_link_id(
                provider=outcome.provider,
                link_id=outcome.provider_payment_link_id,
            )
            if by_link is not None:
                return by_link

        # Unmatchable. Raised rather than auto-creating a payment row: an
        # outcome we cannot tie to an order is a genuine anomaly (wrong
        # webhook endpoint, wrong Razorpay account, a manual dashboard
        # payment), and inventing a row would hide it.
        raise NotFoundError(
            "No payment attempt matches this provider outcome.",
            details={
                "provider": outcome.provider.value,
                "provider_payment_id": outcome.provider_payment_id,
                "provider_payment_link_id": outcome.provider_payment_link_id,
            },
        )

    # -- escalation helper -------------------------------------------------

    @staticmethod
    def escalation_for(result: OutcomeResult) -> tuple[str, TicketPriority, str] | None:
        """Ticket parameters for an anomalous outcome, or ``None``.

        Returns data rather than creating the ticket so this service stays
        free of a dependency on ``support_service`` (and so the webhook
        handler decides, in one place, what gets escalated).
        """
        if not result.needs_escalation:
            return None
        if result.is_duplicate:
            return (
                DOUBLE_PAYMENT_REASON,
                TicketPriority.HIGH,
                f"Customer was charged twice for order {result.order.reference}. "
                f"Payment {result.payment.id} ({result.payment.amount} "
                f"{result.payment.currency}) needs a refund.",
            )
        return (
            "PAYMENT_AMOUNT_MISMATCH",
            TicketPriority.HIGH,
            f"Order {result.order.reference} expected "
            f"{result.payment.amount} {result.payment.currency} but the "
            f"provider settled a different amount. Needs reconciliation.",
        )
