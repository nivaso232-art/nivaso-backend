"""Allowed state transitions for orders and conversations.

Two separate machines, deliberately (rule 8). A customer asking "BTW do you
have RDR 2?" while an order sits at PAYMENT_PENDING moves the *conversation*
to PRODUCT_ENQUIRY and leaves the *order* exactly where it was. Collapsing
these into one state field is what makes chatbots lose orders.

The order machine is strict - it guards money. The conversation machine is
permissive, because real conversations wander.
"""

from __future__ import annotations

from app.core.errors import InvalidStateTransition
from app.models.enums import (
    ConversationState,
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
)

# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset(
        {
            OrderStatus.PENDING_CONFIRMATION,
            OrderStatus.PAYMENT_PENDING,
            OrderStatus.CANCELLED,
        }
    ),
    OrderStatus.PENDING_CONFIRMATION: frozenset(
        {OrderStatus.PAYMENT_PENDING, OrderStatus.CANCELLED}
    ),
    OrderStatus.PAYMENT_PENDING: frozenset(
        {OrderStatus.PAID, OrderStatus.PAYMENT_FAILED, OrderStatus.CANCELLED}
    ),
    # A failed payment is recoverable - the customer retries and we go back to
    # PAYMENT_PENDING with a *new* payments row (rule 6).
    OrderStatus.PAYMENT_FAILED: frozenset(
        {OrderStatus.PAYMENT_PENDING, OrderStatus.CANCELLED}
    ),
    # Note what is missing: PAID -> CANCELLED. Money has moved, so the only
    # way out is REFUNDED, which is a human decision, not an agent one.
    OrderStatus.PAID: frozenset({OrderStatus.FULFILLED, OrderStatus.REFUNDED}),
    OrderStatus.FULFILLED: frozenset({OrderStatus.REFUNDED}),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REFUNDED: frozenset(),
}


def can_transition_order(current: OrderStatus, target: OrderStatus) -> bool:
    return target in ORDER_TRANSITIONS.get(current, frozenset())


def assert_order_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Raise unless the transition is legal.

    A no-op transition (current == target) is allowed and silently accepted:
    webhook redelivery means "mark this PAID" can legitimately arrive twice,
    and the second one should not be an error.
    """
    if current == target:
        return
    if not can_transition_order(current, target):
        raise InvalidStateTransition(
            f"Cannot move order from {current.value} to {target.value}",
            details={"from": current.value, "to": target.value},
        )


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------
PAYMENT_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset(
        {
            PaymentStatus.PROCESSING,
            PaymentStatus.SUCCESS,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
        }
    ),
    PaymentStatus.PROCESSING: frozenset(
        {PaymentStatus.SUCCESS, PaymentStatus.FAILED, PaymentStatus.CANCELLED}
    ),
    PaymentStatus.SUCCESS: frozenset({PaymentStatus.REFUNDED}),
    # Terminal. A retry is a new row, never a resurrection of this one.
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.CANCELLED: frozenset(),
    PaymentStatus.REFUNDED: frozenset(),
}


def assert_payment_transition(current: PaymentStatus, target: PaymentStatus) -> None:
    if current == target:
        return
    if target not in PAYMENT_TRANSITIONS.get(current, frozenset()):
        raise InvalidStateTransition(
            f"Cannot move payment from {current.value} to {target.value}",
            details={"from": current.value, "to": target.value},
        )


# --------------------------------------------------------------------------
# Fulfillment
# --------------------------------------------------------------------------
FULFILLMENT_TRANSITIONS: dict[FulfillmentStatus, frozenset[FulfillmentStatus]] = {
    FulfillmentStatus.PENDING: frozenset(
        {FulfillmentStatus.READY, FulfillmentStatus.FAILED}
    ),
    FulfillmentStatus.READY: frozenset(
        {FulfillmentStatus.DELIVERED, FulfillmentStatus.FAILED}
    ),
    FulfillmentStatus.FAILED: frozenset({FulfillmentStatus.READY}),
    FulfillmentStatus.DELIVERED: frozenset(),
}


def assert_fulfillment_transition(
    current: FulfillmentStatus, target: FulfillmentStatus
) -> None:
    if current == target:
        return
    if target not in FULFILLMENT_TRANSITIONS.get(current, frozenset()):
        raise InvalidStateTransition(
            f"Cannot move fulfillment from {current.value} to {target.value}",
            details={"from": current.value, "to": target.value},
        )


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------
# Permissive by design: from any live state a customer may ask about a new
# product, raise a support issue, or ask for a human. Only COMPLETED is
# closed, and only HUMAN_HANDOFF restricts where you can go next (the AI
# should not quietly take the conversation back from a human agent).
_LIVE_STATES = frozenset(
    {
        ConversationState.NEW,
        ConversationState.PRODUCT_ENQUIRY,
        ConversationState.WAITING_CONFIRMATION,
        ConversationState.PAYMENT_PENDING,
        ConversationState.PAYMENT_VERIFICATION,
        ConversationState.FULFILLMENT,
        ConversationState.SUPPORT,
    }
)

CONVERSATION_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    state: _LIVE_STATES
    | {ConversationState.HUMAN_HANDOFF, ConversationState.COMPLETED}
    for state in _LIVE_STATES
}
CONVERSATION_TRANSITIONS[ConversationState.HUMAN_HANDOFF] = frozenset(
    {ConversationState.SUPPORT, ConversationState.COMPLETED}
)
CONVERSATION_TRANSITIONS[ConversationState.COMPLETED] = frozenset(
    {ConversationState.SUPPORT, ConversationState.PRODUCT_ENQUIRY}
)


def can_transition_conversation(
    current: ConversationState, target: ConversationState
) -> bool:
    return target in CONVERSATION_TRANSITIONS.get(current, frozenset())


def assert_conversation_transition(
    current: ConversationState, target: ConversationState
) -> None:
    if current == target:
        return
    if not can_transition_conversation(current, target):
        raise InvalidStateTransition(
            f"Cannot move conversation from {current.value} to {target.value}",
            details={"from": current.value, "to": target.value},
        )
