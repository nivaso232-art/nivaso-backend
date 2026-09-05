"""AI Playbook rules — super-admin-configurable instructions injected into
every agent system prompt for a given scope (global / plan / business).

Three-tier resolution (lower scope wins for the same trigger):
  global   → applies to every business on every plan
  plan     → applies to all businesses on a specific tier
  business → applies to one business only

Rules are combined, de-duplicated by trigger (business beats plan beats global),
sorted by priority, and appended to the cached system-prompt block so all AI
models receive the same behavioural guidance.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    pass


class BusinessRule(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_rules"
    __table_args__ = (
        Index("ix_business_rules_scope", "scope"),
        Index("ix_business_rules_business_id", "business_id"),
        Index("ix_business_rules_plan", "plan"),
    )

    # ── Scope ────────────────────────────────────────────────────────────────
    # "global" | "plan" | "business"
    scope: Mapped[str] = mapped_column(Text, nullable=False)

    # Set when scope="plan" — the plan tier this rule targets.
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set when scope="business" — the business this rule targets.
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=True,
    )

    # ── Content ───────────────────────────────────────────────────────────────
    # Short human-readable label (e.g. "orders_disabled", "ticket_cancel").
    # Used for deduplication: a business-scoped rule with the same trigger as a
    # global rule will override the global one.
    trigger: Mapped[str] = mapped_column(Text, nullable=False)

    # The instruction text injected verbatim into the system prompt.
    instruction: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional entitlement condition that must be true for this rule to apply,
    # e.g. "orders.enabled=false" or "channel.payments=false".
    # Evaluated at runtime against the business's resolved entitlements.
    feature_condition: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Ordering & state ──────────────────────────────────────────────────────
    # Lower number = higher priority. Rules are injected in ascending priority
    # order so more important rules appear first in the prompt.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    # Audit: super-admin username who last modified this rule.
    updated_by: Mapped[str] = mapped_column(Text, nullable=False, default="super-admin")
