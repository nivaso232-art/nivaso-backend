"""Automatic credential delivery for paid orders.

When an order is PAID this allocates a reusable account from the vault for each
line item, records the delivery on the order's ``fulfillment`` (storing only the
credential *ids*, never the secret — rule 10), moves the order to FULFILLED, and
returns the decrypted logins so the caller can send them to the customer.

Idempotent: a second call for an already-DELIVERED order re-reveals the same
accounts rather than allocating new ones.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import FulfillmentStatus
from app.models.order import Order
from app.repositories.orders import OrderRepository
from app.services.credential_service import CredentialService
from app.services.fulfillment_service import FulfillmentService

log = structlog.get_logger(__name__)


@dataclass
class DeliveredItem:
    product_name: str
    username: str
    password: str


@dataclass
class DeliveryResult:
    order_reference: str
    delivered: bool = False
    already_delivered: bool = False
    out_of_stock: bool = False
    items: list[DeliveredItem] = field(default_factory=list)
    missing_products: list[str] = field(default_factory=list)


class DeliveryService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.orders = OrderRepository(session, business_id)
        self.fulfillment = FulfillmentService(session, business_id)
        self.credentials = CredentialService(session, business_id)

    async def deliver_for_order(self, order: Order) -> DeliveryResult:
        if not order.is_paid:
            return DeliveryResult(order.reference)

        order_full = await self.orders.get_with_items(order.id) or order
        items = list(order_full.items)

        fulfillment = await self.fulfillment.create_for_paid_order(order_full)

        # Idempotent: already delivered → re-reveal from the stored refs.
        if fulfillment.status is FulfillmentStatus.DELIVERED:
            delivered: list[DeliveredItem] = []
            for entry in fulfillment.metadata_.get("credential_refs", []):
                cred = await self.credentials.reveal(uuid.UUID(entry["credential_id"]))
                if cred is not None:
                    delivered.append(
                        DeliveredItem(
                            entry.get("product_name", ""), cred.username, cred.password
                        )
                    )
            return DeliveryResult(
                order_full.reference,
                delivered=True,
                already_delivered=True,
                items=delivered,
            )

        # Pre-flight: verify every product has enough free slots before we
        # allocate anything, so a partly-fillable order doesn't burn slots.
        required: Counter[uuid.UUID] = Counter()
        names: dict[uuid.UUID, str] = {}
        missing: set[str] = set()
        for item in items:
            if item.product_id is None:
                missing.add(item.product_name)
                continue
            required[item.product_id] += item.quantity
            names[item.product_id] = item.product_name

        for product_id, qty in required.items():
            if await self.credentials.free_slots(product_id) < qty:
                missing.add(names[product_id])

        if missing:
            await self.fulfillment.mark_failed(
                fulfillment, reason=f"Out of stock: {', '.join(sorted(missing))}"
            )
            log.warning(
                "delivery_out_of_stock",
                reference=order_full.reference,
                missing=sorted(missing),
            )
            return DeliveryResult(
                order_full.reference, out_of_stock=True, missing_products=sorted(missing)
            )

        # Allocate a slot per unit and collect the decrypted logins.
        delivered = []
        refs: list[dict[str, str]] = []
        for item in items:
            for _ in range(item.quantity):
                alloc = await self.credentials.allocate(item.product_id)  # type: ignore[arg-type]
                if alloc is None:
                    # Lost a race after pre-flight — abort so the whole delivery
                    # rolls back rather than half-completing.
                    raise RuntimeError(
                        f"No credential slot for {item.product_name} after pre-flight"
                    )
                delivered.append(
                    DeliveredItem(item.product_name, alloc.username, alloc.password)
                )
                refs.append(
                    {
                        "credential_id": str(alloc.credential_id),
                        "product_name": item.product_name,
                    }
                )

        # PENDING -> READY -> DELIVERED (the state machine has no PENDING->DELIVERED).
        await self.fulfillment.mark_ready(fulfillment)
        await self.fulfillment.mark_delivered(
            fulfillment,
            delivered_by="auto",
            metadata={"credential_refs": refs, "delivery_method": "auto"},
        )
        log.info(
            "delivery_completed", reference=order_full.reference, count=len(delivered)
        )
        return DeliveryResult(order_full.reference, delivered=True, items=delivered)


def format_delivery_message(reference: str, items: list[DeliveredItem]) -> str:
    """Customer-facing message carrying the login details."""
    lines = ["✅ Payment received! Here are your game login details:", ""]
    for item in items:
        lines.append(f"🎮 *{item.product_name}*")
        lines.append(f"   ID: {item.username}")
        lines.append(f"   Password: {item.password}")
        lines.append("")
    lines.append(f"Order: {reference}")
    lines.append("Please do not change the account password. Enjoy! 🙌")
    return "\n".join(lines).strip()
