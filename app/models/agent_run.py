"""One row per agent turn: tokens, latency, outcome.

Also not in the original list. It is cheap now and expensive to retrofit,
because the interesting questions only get asked once there is traffic:

* what does a conversation cost, and which tenant is expensive?
* is prompt caching actually working? (``cache_read_tokens`` stuck at 0 means
  something volatile crept into the cached prefix)
* how many tool iterations does a real purchase take?
* how often does the loop hit ``max_iterations`` instead of ``end_turn``?
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin

# Claude Opus 5 list price, USD per million tokens. Used only for rough
# in-dashboard cost estimates - billing truth lives in the Anthropic console.
USD_PER_MTOK_INPUT = 5.00
USD_PER_MTOK_OUTPUT = 25.00


class AgentRun(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_conversation_id_created_at", "conversation_id", "created_at"),
        Index("ix_agent_runs_business_id_created_at", "business_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    effort: Mapped[str | None] = mapped_column(String(16))

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    cache_creation_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # Trips through the tool loop. Equal to max_iterations means the loop was
    # cut off rather than finishing on its own - worth alerting on.
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # end_turn | max_tokens | tool_use | pause_turn | refusal
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    # Anthropic request id(s) for this turn, so a bad reply can be reported
    # upstream with something actionable attached.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    @property
    def estimated_usd(self) -> float:
        """Rough turn cost. Cached reads bill at ~0.1x input."""
        billed_input = self.input_tokens + self.cache_creation_tokens * 1.25
        cached = self.cache_read_tokens * 0.1
        return (
            (billed_input + cached) / 1_000_000 * USD_PER_MTOK_INPUT
            + self.output_tokens / 1_000_000 * USD_PER_MTOK_OUTPUT
        )
