"""The tenant root. Everything else hangs off this table."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import BusinessStatus

if TYPE_CHECKING:
    from app.models.business_channel import BusinessChannel
    from app.models.customer import Customer
    from app.models.knowledge import Knowledge
    from app.models.product import Product


class Business(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "businesses"

    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Asia/Kolkata"
    )
    status: Mapped[BusinessStatus] = mapped_column(
        pg_enum(BusinessStatus, "business_status"),
        nullable=False,
        server_default=BusinessStatus.ACTIVE.value,
    )

    # Per-tenant configuration that does not deserve a column yet: agent tone,
    # supported languages, default currency, business hours, escalation rules.
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    products: Mapped[list[Product]] = relationship(
        back_populates="business", cascade="all, delete-orphan", passive_deletes=True
    )
    customers: Mapped[list[Customer]] = relationship(
        back_populates="business", cascade="all, delete-orphan", passive_deletes=True
    )
    knowledge_articles: Mapped[list[Knowledge]] = relationship(
        back_populates="business", cascade="all, delete-orphan", passive_deletes=True
    )
    channels_config: Mapped[list[BusinessChannel]] = relationship(
        back_populates="business", cascade="all, delete-orphan", passive_deletes=True
    )
