"""Catalog.

Named ``products`` rather than ``catalog_items``: domain differences are
absorbed by ``category`` + ``metadata``, not by new tables.

    Gaming       category='Game'      metadata={platform, edition}
    Real estate  category='Apartment' metadata={bhk, locality, furnishing}
    Restaurant   category='Biryani'   metadata={spice_level, serves}
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import ProductStatus

if TYPE_CHECKING:
    from app.models.business import Business

# Weighted full-text document. This is the no-embeddings search path:
#   A = name      (a query naming the product should dominate)
#   B = category + sku
#   C = description
#
# The regconfig is the literal 'english' rather than a column reference so the
# expression stays IMMUTABLE, which Postgres requires for a STORED generated
# column. Every input is coalesced - a NULL anywhere would null the whole
# tsvector and silently drop the row from search results.
_PRODUCT_SEARCH_DOC = """
    setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(category, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(sku, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(description, '')), 'C')
"""


class Product(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "sku", name="uq_products_business_id_sku"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        Index("ix_products_business_id_status", "business_id", "status"),
        Index("ix_products_business_id_category", "business_id", "category"),
        # GIN index on search_doc and the trigram index on name are created in
        # migration 0002 (Alembic does not autogenerate GIN opclasses).
    )

    sku: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="INR"
    )

    status: Mapped[ProductStatus] = mapped_column(
        pg_enum(ProductStatus, "product_status"),
        nullable=False,
        server_default=ProductStatus.ACTIVE.value,
    )
    category: Mapped[str | None] = mapped_column(String(128))

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    # Generated, never written by the application.
    # deferred=True: asyncpg has no native TSVECTOR codec so loading it
    # eagerly raises. This column is only referenced in SQL expressions
    # (WHERE search_doc @@ tsquery) — never read as a Python value.
    search_doc: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_PRODUCT_SEARCH_DOC, persisted=True), deferred=True
    )

    business: Mapped[Business] = relationship(back_populates="products")

    @property
    def is_sellable(self) -> bool:
        return self.status is ProductStatus.ACTIVE

    def price_in_minor_units(self) -> int:
        """Paise for Razorpay. Converted only at the provider boundary."""
        return int((self.price * 100).to_integral_value())
