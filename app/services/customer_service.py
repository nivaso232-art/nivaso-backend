"""Customer identity resolution.

The job: turn "a message arrived from WhatsApp id 919876543210" into a
``(Customer, CustomerChannel)`` pair, creating them on first contact.

Edge case 21 (same human on WhatsApp and Telegram) is handled by
:meth:`CustomerService.link_channel` - two channel rows, one customer.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.customer import Customer, CustomerChannel
from app.models.enums import Channel
from app.repositories.customers import CustomerChannelRepository, CustomerRepository

log = structlog.get_logger(__name__)


class CustomerService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.customers = CustomerRepository(session, business_id)
        self.channels = CustomerChannelRepository(session, business_id)

    async def resolve_or_create(
        self,
        *,
        channel: Channel,
        external_user_id: str,
        display_name: str | None = None,
        phone: str | None = None,
    ) -> tuple[Customer, CustomerChannel]:
        """Find or create the customer behind a channel handle.

        Called on every inbound message, so the happy path is one indexed
        lookup and nothing else.

        Order of resolution matters:

        1. Known channel handle -> done, this is a returning customer.
        2. Unknown handle but a matching phone on an existing customer -> link
           the new channel to that customer. This is what makes edge case 21
           work automatically for WhatsApp (whose ``external_user_id`` *is* the
           phone number) without asking the customer to prove anything.
        3. Otherwise -> a new customer and a new channel.
        """
        existing = await self.channels.get_with_customer(
            channel=channel, external_user_id=external_user_id
        )
        if existing is not None:
            if display_name and not existing.display_name:
                existing.display_name = display_name
                await self.session.flush()
            return existing.customer, existing

        # WhatsApp's wa_id is the phone number, so infer it when not supplied.
        inferred_phone = phone or (
            self._normalize_phone(external_user_id)
            if channel is Channel.WHATSAPP
            else None
        )

        customer: Customer | None = None
        if inferred_phone:
            customer = await self.customers.get_by_phone(inferred_phone)

        if customer is None:
            customer = Customer(
                name=display_name,
                phone=inferred_phone,
            )
            await self.customers.add(customer)
            log.info("customer_created", customer_id=str(customer.id))

        channel_row = await self._insert_channel(
            customer_id=customer.id,
            channel=channel,
            external_user_id=external_user_id,
            display_name=display_name,
        )
        return customer, channel_row

    async def _insert_channel(
        self,
        *,
        customer_id: uuid.UUID,
        channel: Channel,
        external_user_id: str,
        display_name: str | None,
    ) -> CustomerChannel:
        """INSERT a channel row, tolerating a concurrent duplicate.

        Two webhooks from a first-time customer can arrive at once - Meta
        batches messages - and both would find no channel row. The unique
        index decides; the loser re-reads what the winner wrote.
        """
        row = CustomerChannel(
            customer_id=customer_id,
            channel=channel,
            external_user_id=external_user_id,
            display_name=display_name,
        )
        savepoint = await self.session.begin_nested()
        try:
            await self.channels.add(row)
            await savepoint.commit()
            return row
        except IntegrityError:
            await savepoint.rollback()
            existing = await self.channels.get_by_external_id(
                channel=channel, external_user_id=external_user_id
            )
            if existing is None:  # pragma: no cover - would mean a different violation
                raise
            log.info(
                "customer_channel_race_resolved",
                channel=channel.value,
                external_user_id=external_user_id,
            )
            return existing

    async def link_channel(
        self,
        *,
        customer_id: uuid.UUID,
        channel: Channel,
        external_user_id: str,
        display_name: str | None = None,
    ) -> CustomerChannel:
        """Attach another channel identity to an existing customer.

        Deliberately an explicit operation rather than something inferred: for
        Telegram there is no phone number to match on, so merging two
        identities is a claim about who someone is. Wire this to a verification
        step (an OTP to the known phone) before exposing it to customers -
        otherwise anyone who guesses a phone number inherits that customer's
        order history.
        """
        customer = await self.customers.get(customer_id)
        if customer is None:
            raise NotFoundError(
                "Customer not found.", details={"customer_id": str(customer_id)}
            )

        existing = await self.channels.get_with_customer(
            channel=channel, external_user_id=external_user_id
        )
        if existing is not None:
            if existing.customer_id != customer_id:
                raise ConflictError(
                    "This channel identity already belongs to another customer.",
                    details={
                        "channel": channel.value,
                        "external_user_id": external_user_id,
                    },
                )
            return existing

        return await self._insert_channel(
            customer_id=customer_id,
            channel=channel,
            external_user_id=external_user_id,
            display_name=display_name,
        )

    @staticmethod
    def _normalize_phone(raw: str) -> str:
        """E.164-ish normalisation.

        WhatsApp sends "919876543210" (no plus). Stored with the plus so the
        column holds one consistent format regardless of channel.
        """
        digits = "".join(ch for ch in raw if ch.isdigit())
        return f"+{digits}" if digits else raw
