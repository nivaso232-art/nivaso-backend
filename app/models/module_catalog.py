"""Module catalog — the extensible, admin-visible list of requestable modules
and integrations.

This is a global (non-tenant) table: it is the single source of truth for
what a business may select in ``SignupIn.requested_modules`` (see
``app/api/auth.py``) and what the unauthenticated signup page renders via
``GET /module-catalog`` (see ``app/api/module_catalog.py``). Adding a new
integration or module going forward means inserting a new catalog row via
migration — no code changes required to make it requestable.

``category`` distinguishes catalog/CRM feature areas (``module``) from
channel/provider integrations (``integration``), which the frontend uses to
group the signup form into two sections.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin, pg_enum


class ModuleCatalogCategory(StrEnum):
    MODULE = "module"
    INTEGRATION = "integration"


class ModuleCatalogEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "module_catalog"

    # A FeatureFlag key, e.g. "module.services" or "channel.instagram".
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    category: Mapped[ModuleCatalogCategory] = mapped_column(
        pg_enum(ModuleCatalogCategory, "module_catalog_category"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
