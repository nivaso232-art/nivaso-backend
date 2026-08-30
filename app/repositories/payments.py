"""Payment reads.

Note what is absent: there is no ``update_status``. Status transitions live in
``payment_service`` and are reachable only from the Razorpay webhook handler
(rule 2). A repository method here would be a way around that.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.models.enums import PaymentProvider, PaymentStatus
from app.models.payment import Payment
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def get_by_provider_payment_id(
        self, *, provider: PaymentProvider, provider_payment_id: str
    ) -> Payment | None:
        """Idempotency lookup for webhook handling.

        Deliberately **not** tenant-scoped: a Razorpay webhook arrives with no
        tenant context, and the provider id is globally unique. The
        ``business_id`` on the row found here is what establishes the tenant
        for everything downstream.
        """
        stmt = select(Payment).where(
            Payment.provider == provider,
            Payment.provider_payment_id == provider_payment_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_provider_link_id(
        self, *, provider: PaymentProvider, link_id: str
    ) -> Payment | None:
        """Match a payment-link webhook back to the PENDING attempt.

        A link-based flow has no ``provider_payment_id`` until money moves, so
        the link id is the only handle on the attempt we created earlier.
        """
        stmt = select(Payment).where(
            Payment.provider == provider,
            Payment.provider_payment_link_id == link_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_order(self, order_id: uuid.UUID) -> Sequence[Payment]:
        """Every attempt, oldest first - the audit trail for rule 6."""
        stmt = (
            self._scoped()
            .where(Payment.order_id == order_id)
            .order_by(Payment.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_successful_for_order(self, order_id: uuid.UUID) -> Payment | None:
        """The attempt that actually paid, if any.

        ``is_duplicate`` is excluded so this returns the *first* success - the
        one that legitimately paid the order - rather than a later double
        charge awaiting refund.
        """
        stmt = (
            self._scoped()
            .where(
                Payment.order_id == order_id,
                Payment.status == PaymentStatus.SUCCESS,
                Payment.is_duplicate.is_(False),
            )
            .order_by(Payment.created_at)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_open_for_order(self, order_id: uuid.UUID) -> Payment | None:
        """An attempt still awaiting an outcome.

        Reused instead of creating a second link when a customer taps "pay"
        twice in thirty seconds - otherwise every impatient tap manufactures a
        new payment row.
        """
        stmt = (
            self._scoped()
            .where(
                Payment.order_id == order_id,
                Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.PROCESSING]),
            )
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_needing_refund(self, *, limit: int = 100) -> Sequence[Payment]:
        """Operational queue for double charges (rule 7)."""
        stmt = (
            self._scoped()
            .where(Payment.needs_refund.is_(True))
            .order_by(Payment.created_at)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
