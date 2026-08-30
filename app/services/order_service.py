"""Order lifecycle.

This module is where **rule 1** is enforced: the AI never sets a price.

``create_order`` accepts only ``(product_id, quantity)`` pairs. Unit prices are
read from the ``products`` table inside the same transaction that writes the
order, so there is no window in which a model-supplied number could be trusted
and no parameter through which one could arrive. If the agent tells a customer
"₹149" while the database says 229, the order is still 229 - the lie stays a
lie in chat instead of becoming a lie in the ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.ids import generate_order_reference
from app.models.enums import OrderStatus, ProductStatus
from app.models.order import Order, OrderItem
from app.repositories.orders import OrderRepository
from app.repositories.products import ProductRepository
from app.services.state_machine import assert_order_transition

log = structlog.get_logger(__name__)

MAX_QUANTITY_PER_LINE = 20
MAX_LINES_PER_ORDER = 20
# Reference collisions are ~1 in a billion per month; three attempts is
# generous. Bounded so a genuinely broken generator fails loudly instead of
# spinning.
_REFERENCE_ATTEMPTS = 3


@dataclass(frozen=True)
class OrderLineRequest:
    """What the caller (agent tool or admin API) is allowed to ask for.

    Note the absence of a price field. This dataclass is the type-level
    statement of rule 1.
    """

    product_id: uuid.UUID
    quantity: int = 1


class OrderService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.orders = OrderRepository(session, business_id)
        self.products = ProductRepository(session, business_id)

    # -- creation ---------------------------------------------------------

    async def create_order(
        self,
        *,
        customer_id: uuid.UUID,
        lines: list[OrderLineRequest],
        conversation_id: uuid.UUID | None = None,
        discount: Decimal = Decimal("0"),
    ) -> Order:
        """Create an order, pricing every line from the database.

        Caller is responsible for the transaction (wrap in a UnitOfWork).
        """
        if not lines:
            raise ValidationError("An order needs at least one line item.")
        if len(lines) > MAX_LINES_PER_ORDER:
            raise ValidationError(
                f"An order cannot exceed {MAX_LINES_PER_ORDER} line items.",
                details={"lines": len(lines)},
            )

        # Merge duplicate product ids rather than creating two lines for the
        # same product - the model sometimes emits [{p1,1},{p1,1}] for "two of
        # those", and 2x1 reads better on an invoice than 1+1.
        merged: dict[uuid.UUID, int] = {}
        for line in lines:
            if line.quantity < 1:
                raise ValidationError(
                    "Quantity must be at least 1.",
                    details={"product_id": str(line.product_id)},
                )
            merged[line.product_id] = merged.get(line.product_id, 0) + line.quantity

        for product_id, quantity in merged.items():
            if quantity > MAX_QUANTITY_PER_LINE:
                raise ValidationError(
                    f"Quantity for a single product cannot exceed "
                    f"{MAX_QUANTITY_PER_LINE}.",
                    details={"product_id": str(product_id), "quantity": quantity},
                )

        # Tenant-scoped batch fetch. A product belonging to another business
        # simply does not come back, so the missing-id check below is also the
        # cross-tenant check (rule 4).
        products = await self.products.get_many(list(merged.keys()))
        found = {product.id: product for product in products}

        missing = set(merged) - set(found)
        if missing:
            raise NotFoundError(
                "One or more products were not found.",
                details={"product_ids": [str(pid) for pid in sorted(missing)]},
            )

        unavailable = [
            product.name
            for product in products
            if product.status is not ProductStatus.ACTIVE
        ]
        if unavailable:
            raise ConflictError(
                "One or more products are not currently available.",
                details={"products": unavailable},
            )

        currencies = {product.currency for product in products}
        if len(currencies) > 1:
            raise ValidationError(
                "All items in an order must share a currency.",
                details={"currencies": sorted(currencies)},
            )
        currency = currencies.pop()

        items: list[OrderItem] = []
        subtotal = Decimal("0")
        for product_id, quantity in merged.items():
            product = found[product_id]
            # THE authoritative price. Read here, from the row, every time.
            unit_price = product.price
            line_total = unit_price * quantity
            subtotal += line_total
            items.append(
                OrderItem(
                    product_id=product.id,
                    # Snapshots - immune to a later price or name change.
                    product_name=product.name,
                    product_sku=product.sku,
                    unit_price=unit_price,
                    quantity=quantity,
                    total=line_total,
                )
            )

        if discount < 0:
            raise ValidationError("Discount cannot be negative.")
        if discount > subtotal:
            raise ValidationError(
                "Discount cannot exceed the order subtotal.",
                details={"subtotal": str(subtotal), "discount": str(discount)},
            )

        order = await self._insert_with_reference(
            customer_id=customer_id,
            conversation_id=conversation_id,
            currency=currency,
            subtotal=subtotal,
            discount=discount,
            items=items,
        )

        log.info(
            "order_created",
            order_id=str(order.id),
            reference=order.reference,
            total=str(order.total),
            lines=len(items),
        )
        return order

    async def _insert_with_reference(
        self,
        *,
        customer_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        currency: str,
        subtotal: Decimal,
        discount: Decimal,
        items: list[OrderItem],
    ) -> Order:
        """INSERT, retrying on a reference collision.

        The retry is driven by the unique index rather than a pre-check, so two
        concurrent orders that happen to generate the same reference cannot
        both pass a "does it exist?" test and then collide on commit.
        """
        last_error: IntegrityError | None = None

        for attempt in range(_REFERENCE_ATTEMPTS):
            order = Order(
                customer_id=customer_id,
                conversation_id=conversation_id,
                reference=generate_order_reference(),
                status=OrderStatus.PENDING_CONFIRMATION,
                currency=currency,
                subtotal=subtotal,
                discount=discount,
                total=subtotal - discount,
            )
            order.items = items

            savepoint = await self.session.begin_nested()
            try:
                await self.orders.add(order)
                await savepoint.commit()
                return order
            except IntegrityError as exc:
                await savepoint.rollback()
                if "uq_orders_business_id_reference" not in str(exc.orig):
                    raise
                last_error = exc
                log.warning("order_reference_collision", attempt=attempt + 1)

        raise ConflictError(
            "Could not allocate a unique order reference.",
            details={"attempts": _REFERENCE_ATTEMPTS},
        ) from last_error

    # -- transitions ------------------------------------------------------

    async def mark_payment_pending(self, order: Order) -> Order:
        """Called when a payment link has been issued."""
        assert_order_transition(order.status, OrderStatus.PAYMENT_PENDING)
        order.status = OrderStatus.PAYMENT_PENDING
        await self.session.flush()
        return order

    async def cancel_order(self, order: Order, *, reason: str) -> Order:
        """Cancel an unpaid order (edge case 17).

        A paid order cannot be cancelled - only refunded, which is a human
        decision. This is the check that stops an agent from "helpfully"
        cancelling something the customer already paid for.
        """
        if order.is_paid:
            raise ConflictError(
                "This order is already paid and cannot be cancelled. "
                "It needs a refund, which a human agent must authorise.",
                details={"reference": order.reference, "status": order.status.value},
            )
        assert_order_transition(order.status, OrderStatus.CANCELLED)
        order.status = OrderStatus.CANCELLED
        order.metadata_ = {**order.metadata_, "cancellation_reason": reason}
        await self.session.flush()

        log.info("order_cancelled", reference=order.reference, reason=reason)
        return order

    # -- reads ------------------------------------------------------------

    async def get_by_reference_or_raise(self, reference: str) -> Order:
        order = await self.orders.get_by_reference(reference)
        if order is None:
            raise NotFoundError(
                "Order not found.", details={"reference": reference}
            )
        return order

    async def resolve_order(
        self, *, customer_id: uuid.UUID, reference: str | None = None
    ) -> Order:
        """Find the order the customer means.

        With a reference, look it up and verify it belongs to this customer -
        a reference is guessable enough that it must not be a bearer token for
        someone else's order. Without one, fall back to their latest open
        order, which is what "where's my game?" almost always means.
        """
        if reference:
            order = await self.get_by_reference_or_raise(reference)
            if order.customer_id != customer_id:
                raise NotFoundError(
                    "Order not found.", details={"reference": reference}
                )
            return order

        order = await self.orders.get_latest_open_for_customer(customer_id)
        if order is None:
            raise NotFoundError("No open orders found for this customer.")
        return order
