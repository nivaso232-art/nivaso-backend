"""Declarative base, shared column mixins, and enum/money helpers."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, MetaData, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable,
# diffable constraint names instead of relying on Postgres defaults.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        dict[str, Any]: JSONB,
        Decimal: Numeric(12, 2),
    }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


def pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Map a Python StrEnum to a native Postgres enum type.

    ``values_callable`` is essential: without it SQLAlchemy persists the enum
    *member names* (``ACTIVE``) rather than the *values* (``active``), which
    then disagrees with every hand-written SQL query and seed file.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_type=True,
        values_callable=lambda e: [member.value for member in e],
    )


def money() -> Mapped[Decimal]:
    """NUMERIC(12,2). Never float - 0.1 + 0.2 must not cost a customer money."""
    return mapped_column(Numeric(12, 2), nullable=False)


class UUIDMixin:
    """Server-generated UUID primary key.

    ``gen_random_uuid()`` is built into Postgres 13+, so no pgcrypto needed.
    Generated server-side so a raw SQL INSERT behaves like an ORM one.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantMixin:
    """``business_id`` on every tenant-owned table.

    Carried even where it is derivable through a join (``order_items`` is the
    only table that omits it, since it is meaningless without its order). The
    redundancy buys a single uniform tenant predicate: every repository query
    filters ``business_id`` directly, with no join required to know whether a
    row is in scope.
    """

    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
