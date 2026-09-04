"""Login credentials for a business's admin portal."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class BusinessAdmin(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_admins"

    business_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Username is the business slug for easy recall.
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    # bcrypt hash of the plaintext password.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
