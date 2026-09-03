"""Editable plan tier definitions stored in the database.

Each row represents one plan tier (free, starter, pro, enterprise) with the
full set of feature flags that plan grants. When a row exists it overrides the
code-level defaults in ``app/entitlements/flags.py``.

If no row exists for a plan, the code defaults are used as a fallback, which
means the table can be empty and everything still works — it only needs rows
for plans that have been customised.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class PlanDefinition(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "plan_definitions"

    # "free" | "starter" | "pro" | "enterprise" (or any future custom tier)
    plan_name: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )

    # Complete set of feature flags for this plan tier.
    flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # Who last changed this plan definition.
    updated_by: Mapped[str | None] = mapped_column(Text)
