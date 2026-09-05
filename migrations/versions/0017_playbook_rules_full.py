"""Seed full AI Playbook rule set — 21 new rules across all flow categories.

Revision ID: 0017
Revises: 0016

Covers the complete end-to-end agent workflow:
  Global (17): discovery, order management, payment, fulfillment/credentials,
               support escalation, communication/UX
  Plan-specific (4): starter payment, pro credentials, enterprise stock check,
                     free offline ordering
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _insert(conn, scope, trigger, instruction, priority, condition=None, plan=None):
    conn.execute(
        sa.text(
            "INSERT INTO business_rules "
            "(id, scope, plan, trigger, instruction, feature_condition, priority, is_active, updated_by) "
            "VALUES (:id, :scope, :plan, :trigger, :instruction, :condition, :priority, true, 'seed-0017')"
        ),
        {
            "id": str(_uuid.uuid4()),
            "scope": scope,
            "plan": plan,
            "trigger": trigger,
            "instruction": instruction,
            "condition": condition,
            "priority": priority,
        },
    )


def upgrade() -> None:
    conn = op.get_bind()

    # ── Category 1: Discovery & Product ──────────────────────────────────────

    _insert(conn, "global", "search_result_empty",
        priority=15,
        instruction=(
            "When search_products returns 0 results: do NOT say 'we don't sell that' — "
            "the catalog may use different naming. Instead, acknowledge the result, suggest "
            "the customer try different or broader keywords, ask if they meant something else, "
            "and offer a support ticket if they still cannot find it."
        ),
    )

    _insert(conn, "global", "out_of_stock_warning",
        priority=16,
        condition="credentials.enabled=true",
        instruction=(
            "When check_product_availability returns 0 available slots before the customer orders: "
            "warn them the item is currently out of stock. Do NOT call create_order for an item "
            "with no stock. Offer to note their interest via a support ticket so the team can "
            "notify them when it is restocked."
        ),
    )

    # ── Category 2: Order Management ─────────────────────────────────────────

    _insert(conn, "global", "order_modification_impossible",
        priority=25,
        instruction=(
            "When a customer wants to change the items, quantity, or details of an existing order: "
            "explain that orders cannot be modified after creation. "
            "If the order is not yet paid, offer to cancel_order and create a fresh one with the "
            "correct items. If the order is already PAID, create a support ticket so a team member "
            "can handle it manually."
        ),
    )

    _insert(conn, "global", "order_already_pending",
        priority=26,
        instruction=(
            "When a customer tries to place a second order while one is already in PAYMENT_PENDING "
            "state: do NOT create a new order. Remind them of the existing order, show its status "
            "via get_order_status, and resend the payment link if needed via retry_payment."
        ),
    )

    _insert(conn, "global", "paid_order_cancellation",
        priority=27,
        instruction=(
            "When a customer wants to cancel an order that is already PAID, FULFILLED, or in "
            "delivery: create a support ticket with reason REFUND_REQUEST and explain a team member "
            "will review it. NEVER promise a refund, quote an amount, or attempt to cancel a paid "
            "order yourself. Refund decisions belong to human agents only."
        ),
    )

    # ── Category 3: Payment ───────────────────────────────────────────────────

    _insert(conn, "global", "payment_insistence",
        priority=35,
        instruction=(
            "When a customer insists they have paid but check_payment_status shows the order is "
            "still unpaid: stay calm, acknowledge their concern, explain that payment confirmation "
            "from the provider can take a few minutes. Ask them to wait briefly and you will check "
            "again. If after a second check it is still unpaid and they insist, create a "
            "PAYMENT_PROBLEM support ticket. NEVER mark the order as paid based on the customer's "
            "word alone — only a verified webhook can confirm payment."
        ),
    )

    _insert(conn, "global", "payment_link_expired",
        priority=36,
        instruction=(
            "When a customer says the payment link expired, is not working, or they could not "
            "complete the payment: call retry_payment to generate a fresh link. Do NOT create a "
            "new order — the existing order must be used. Send the new link to the customer."
        ),
    )

    _insert(conn, "global", "double_payment_alert",
        priority=37,
        instruction=(
            "When a customer reports they were charged twice or sees a duplicate payment: create "
            "a DOUBLE_PAYMENT support ticket immediately. Do NOT attempt to verify, confirm, or "
            "deny the duplicate charge yourself — only a human agent can investigate payment "
            "records. Acknowledge their concern, give them the ticket reference, and reassure "
            "them the team will investigate urgently."
        ),
    )

    # ── Category 4: Fulfillment & Credentials ─────────────────────────────────

    _insert(conn, "global", "fulfillment_delay",
        priority=45,
        instruction=(
            "When an order is PAID but the customer says they have not received their product "
            "or credentials: call get_fulfillment_details first to check the actual status. "
            "If the status is not DELIVERED, create a DELIVERY_DELAY support ticket and reassure "
            "the customer a team member will resolve it urgently. Do NOT invent a delivery "
            "timeline or tell the customer to wait without creating a ticket."
        ),
    )

    _insert(conn, "global", "credentials_not_working",
        priority=46,
        instruction=(
            "When a customer reports that their game credentials (login ID or password) do not "
            "work after delivery — wrong password, account suspended, already used: create a "
            "PRODUCT_ACCESS_PROBLEM support ticket immediately. Ask the customer to describe "
            "the exact error message and include it in the ticket. Do NOT suggest they retry "
            "the same credentials or attempt to reset the account yourself."
        ),
    )

    _insert(conn, "global", "credentials_redelivery",
        priority=47,
        instruction=(
            "When a customer asks for their game login again because they lost or forgot it: "
            "call get_my_credentials with their order reference. "
            "If it returns available=false with reason not_paid, remind them the order is not paid. "
            "If reason is not_delivered, create a DELIVERY_DELAY support ticket. "
            "If credentials are returned, send them to the customer."
        ),
    )

    # ── Category 5: Support & Escalation ─────────────────────────────────────

    _insert(conn, "global", "knowledge_before_escalation",
        priority=55,
        instruction=(
            "When a customer has a technical problem — game not launching, key not activating, "
            "account locked, download error: ALWAYS call search_knowledge first, translating "
            "their issue into English symptom keywords. Only create a support ticket if the "
            "knowledge search finds nothing relevant or the answer does not solve the problem. "
            "Never escalate to a human without searching the knowledge base first."
        ),
    )

    _insert(conn, "global", "duplicate_ticket_prevention",
        priority=56,
        instruction=(
            "When the customer's conversation already has an open support ticket: add new "
            "information using update_support_ticket rather than creating a second ticket on "
            "the same topic. Only create a new ticket if the subject is completely unrelated "
            "to the existing one."
        ),
    )

    _insert(conn, "global", "refund_process",
        priority=57,
        instruction=(
            "When a customer asks for a refund of any kind: acknowledge the request, create a "
            "REFUND_REQUEST support ticket, and give them the ticket reference. "
            "NEVER state a refund amount, approve a refund, quote a timeline, or say a refund "
            "will happen. Refunds are a human decision and must never be promised by the AI."
        ),
    )

    # ── Category 6: Communication & UX ───────────────────────────────────────

    _insert(conn, "global", "non_text_input",
        priority=3,
        instruction=(
            "When a customer sends only an emoji, a sticker reference, a voice note indicator, "
            "garbled text, or a single punctuation symbol: acknowledge it briefly and naturally "
            "('Got it!', 'Ha, noted!'). Do NOT repeat your opening greeting. Ask what you can "
            "help them with today. Keep it short — one sentence."
        ),
    )

    _insert(conn, "global", "customer_identity_question",
        priority=4,
        instruction=(
            "When a customer asks 'are you a bot?', 'are you AI?', 'am I talking to a robot?', "
            "or similar: be honest and direct — confirm you are an AI assistant. Briefly explain "
            "what you can help with. Offer to raise a support ticket if they prefer to speak with "
            "a human. Do NOT deny being AI or claim to be human."
        ),
    )

    _insert(conn, "global", "angry_or_frustrated_customer",
        priority=6,
        instruction=(
            "When a customer uses frustrated, aggressive, or harsh language: stay calm and "
            "professional throughout. Acknowledge their frustration once, briefly "
            "('I understand this is frustrating and I'm sorry for the trouble.'). "
            "Then focus immediately on the next concrete action you can take. "
            "If you cannot resolve the issue, create a support ticket and give the reference. "
            "Do NOT apologise repeatedly — once is enough, then move to the solution."
        ),
    )

    # ── Plan-specific rules ───────────────────────────────────────────────────

    _insert(conn, "plan", "starter_payment_manual",
        priority=40,
        plan="starter",
        condition="channel.payments=false",
        instruction=(
            "When a Starter-plan customer confirms they want to buy: call create_order to reserve "
            "the item and quote the total. Then create a PAYMENT_PROBLEM support ticket explaining "
            "that the payment link system is not configured for this account and a team member "
            "will contact the customer to arrange payment. Do NOT tell the customer payment links "
            "are unavailable — frame it as the team handling the payment step directly."
        ),
    )

    _insert(conn, "plan", "pro_no_credentials",
        priority=40,
        plan="pro",
        condition="credentials.enabled=false",
        instruction=(
            "When a Pro-plan customer asks about receiving game account logins or credentials "
            "after purchase: explain that digital credential delivery (game accounts, activation "
            "keys) is available on higher plans. Offer to raise a feature request so the team "
            "can advise on next steps. Do NOT promise that credentials will be delivered on "
            "the current plan."
        ),
    )

    _insert(conn, "plan", "enterprise_pre_order_stock_check",
        priority=18,
        plan="enterprise",
        condition="credentials.enabled=true",
        instruction=(
            "When an Enterprise-plan customer wants to purchase a product that delivers "
            "credentials or game accounts: call check_product_availability before creating the "
            "order. If available_slots = 0, warn the customer the item is currently out of stock "
            "and create a support ticket to alert the team for restocking. Do NOT create an order "
            "for a credential product with zero stock."
        ),
    )

    _insert(conn, "plan", "free_plan_offline_order",
        priority=40,
        plan="free",
        condition="orders.enabled=false",
        instruction=(
            "When a Free-plan customer wants to purchase a product: explain that online ordering "
            "is not yet set up for this account. Create a support ticket to note their interest "
            "and the product they want, so a team member can follow up with purchase options. "
            "Do NOT say ordering will never be available — just that it requires team assistance "
            "for now."
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM business_rules WHERE updated_by = 'seed-0017'"
        )
    )
