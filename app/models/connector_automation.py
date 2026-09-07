"""Per-business connector automation flow.

Stores the button/menu workflow tree for a messaging channel. One row per
(business, connector_type) pair. The ``flow`` JSONB column holds the full
node graph; the executor reads it at runtime to drive interactive menus.
"""

from __future__ import annotations

import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.business import Business

_DEFAULT_FLOW: dict[str, Any] = {
    "welcome_message": "",
    "fallback_message": "Sorry, I didn't understand. Please choose an option.",
    "nodes": [],
}


class ConnectorAutomation(UUIDMixin, TimestampMixin, Base):
    """One row per (business_id, connector_type) pair.

    connector_type values: 'whatsapp' | 'telegram' | 'web'

    flow JSONB schema:
    {
      "welcome_message": str,
      "fallback_message": str,
      "nodes": [
        {
          "id": str,
          "type": "menu" | "message" | "action",
          "message": str,
          "options": [
            {
              "id": str,
              "label": str,
              "description": str,     # optional
              "next": str,            # node id to navigate to
              "action": {             # optional: execute a module function
                "module": str,
                "function": str,
                "input_param": str    # optional
              }
            }
          ]
        }
      ]
    }
    """

    __tablename__ = "connector_automations"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "connector_type",
            name="uq_connector_automations_business_id_connector_type",
        ),
        Index("ix_connector_automations_business_id", "business_id"),
        Index(
            "ix_connector_automations_business_id_connector_type",
            "business_id",
            "connector_type",
        ),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="Default Flow"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    flow: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=(
            '{"welcome_message":"","fallback_message":'
            '"Sorry, I didn\'t understand. Please choose an option.","nodes":[]}'
        ),
    )

    business: Mapped["Business"] = relationship(back_populates="connector_automations")
