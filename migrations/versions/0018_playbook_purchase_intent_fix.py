"""Strengthen playbook rules for purchase-intent handling.

Revision ID: 0018
Revises: 0017

Two rules updated:
  human_handoff_active      — explicit override for purchase intent in HUMAN_HANDOFF state
  duplicate_ticket_prevention — exempt purchase intents from ticket-update-first behavior

Root cause addressed: Groq/Llama models in HUMAN_HANDOFF state default to ticket escalation
even when the customer clearly wants to buy. These stronger instructions correct that.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPDATES = {
    "human_handoff_active": (
        "HUMAN_HANDOFF state means a support ticket is open — it does NOT mean the conversation "
        "is over or that every future message must be escalated. "
        "CRITICAL — purchase intent override: if the customer clearly wants to buy a product "
        "(says 'I want to buy', 'buy it', 'order it', 'place an order', or confirms a purchase), "
        "you MUST proceed with the full purchase flow immediately: call create_order, quote the "
        "total, call create_payment_link, send the link. "
        "Do NOT create another support ticket just because one is already open. "
        "Only escalate if the customer has a non-purchase problem or explicitly asks for a human."
    ),
    "duplicate_ticket_prevention": (
        "When the conversation has an open support ticket AND the customer sends a new message: "
        "FIRST check if the message is a PURCHASE INTENT (customer wants to buy, order, or pay "
        "for a product). If it IS a purchase intent — ignore the open ticket entirely and "
        "proceed with the full order flow (create_order → create_payment_link). "
        "Only update an existing ticket or create a new one when the customer has a support "
        "problem, complaint, refund request, or question that is NOT a purchase."
    ),
}


def upgrade() -> None:
    conn = op.get_bind()
    for trigger, instruction in _UPDATES.items():
        conn.execute(
            sa.text(
                "UPDATE business_rules SET instruction = :instruction, updated_by = 'migration-0018' "
                "WHERE trigger = :trigger AND scope = 'global'"
            ),
            {"trigger": trigger, "instruction": instruction},
        )


def downgrade() -> None:
    # Restore original instructions
    _ORIGINALS = {
        "human_handoff_active": (
            "HUMAN_HANDOFF state means a support ticket is open — it does NOT mean "
            "the conversation is over. If the customer wants to buy a product, "
            "proceed with the full order flow (search → order → payment) regardless "
            "of conversation state. Do not create another ticket just because one is open."
        ),
        "duplicate_ticket_prevention": (
            "When the conversation already has an open support ticket: add new "
            "information using update_support_ticket rather than creating a second ticket on "
            "the same topic. Only create a new ticket if the subject is completely unrelated "
            "to the existing one."
        ),
    }
    conn = op.get_bind()
    for trigger, instruction in _ORIGINALS.items():
        conn.execute(
            sa.text(
                "UPDATE business_rules SET instruction = :instruction, updated_by = 'migration-0018-downgrade' "
                "WHERE trigger = :trigger AND scope = 'global'"
            ),
            {"trigger": trigger, "instruction": instruction},
        )
