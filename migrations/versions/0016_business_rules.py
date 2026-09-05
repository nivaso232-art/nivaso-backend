"""Create business_rules table for AI Playbook.

Revision ID: 0016
Revises: 0015

Super-admin-configurable rules that are injected into every agent system prompt.
Enables dynamic, non-code behavioral control over all AI models.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_rules",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column(
            "business_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("feature_condition", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_by", sa.Text(), nullable=False, server_default="super-admin"),
    )
    op.create_index("ix_business_rules_scope", "business_rules", ["scope"])
    op.create_index("ix_business_rules_business_id", "business_rules", ["business_id"])
    op.create_index("ix_business_rules_plan", "business_rules", ["plan"])

    # Seed global default rules covering the most common edge cases
    conn = op.get_bind()
    import uuid as _uuid

    _DEFAULTS = [
        {
            "trigger": "ticket_cancel_requested",
            "instruction": (
                "When a customer asks to cancel or close their support ticket: "
                "call update_support_ticket to add the note 'Customer requested cancellation', "
                "then tell them a team member will close it shortly. "
                "NEVER say the ticket is already closed — you cannot close tickets."
            ),
            "feature_condition": None,
            "priority": 10,
        },
        {
            "trigger": "human_handoff_active",
            "instruction": (
                "HUMAN_HANDOFF state means a support ticket is open — it does NOT mean "
                "the conversation is over. If the customer wants to buy a product, "
                "proceed with the full order flow (search → order → payment) regardless "
                "of conversation state. Do not create another ticket just because one is open."
            ),
            "feature_condition": None,
            "priority": 20,
        },
        {
            "trigger": "payments_unavailable",
            "instruction": (
                "When the customer wants to pay and payment links are not available: "
                "still call create_order to reserve the item, read back the total, "
                "then create a support ticket (reason: PAYMENT_PROBLEM) so a team member "
                "can arrange payment manually. NEVER skip the create_order step."
            ),
            "feature_condition": "channel.payments=false",
            "priority": 30,
        },
        {
            "trigger": "orders_disabled",
            "instruction": (
                "When a customer wants to buy but ordering is not enabled for this business: "
                "explain that online ordering is not yet set up. Offer to note their interest "
                "via a support ticket so the team can follow up. Do NOT call create_order."
            ),
            "feature_condition": "orders.enabled=false",
            "priority": 30,
        },
        {
            "trigger": "script_switch_requested",
            "instruction": (
                "When a customer says they cannot read the script you used "
                "(e.g. 'Tamil la msg pannatha', 'dont write Tamil', 'English la sollu'): "
                "immediately switch to the script/language they can read. "
                "Apologise once ('Sorry, switching now!') and do not use the rejected script again."
            ),
            "feature_condition": None,
            "priority": 5,
        },
    ]

    for rule in _DEFAULTS:
        conn.execute(
            sa.text(
                "INSERT INTO business_rules "
                "(id, scope, trigger, instruction, feature_condition, priority, is_active, updated_by) "
                "VALUES (:id, 'global', :trigger, :instruction, :condition, :priority, true, 'seed')"
            ),
            {
                "id": str(_uuid.uuid4()),
                "trigger": rule["trigger"],
                "instruction": rule["instruction"],
                "condition": rule["feature_condition"],
                "priority": rule["priority"],
            },
        )


def downgrade() -> None:
    op.drop_index("ix_business_rules_plan", "business_rules")
    op.drop_index("ix_business_rules_business_id", "business_rules")
    op.drop_index("ix_business_rules_scope", "business_rules")
    op.drop_table("business_rules")
