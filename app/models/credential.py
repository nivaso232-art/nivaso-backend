"""Reusable game-account credential vault.

This is the "secrets manager" rule 10 points at: an isolated pool of account
credentials, one row per account, whose **password is encrypted at rest**
(``secret_encrypted`` — a Fernet token; see ``app/core/crypto.py``). Nothing
else joins to this table; ``fulfillments`` only ever stores a ``credential_ref``
(this row's id), never the secret.

A "reusable" account can be handed to more than one customer — ``capacity`` is
how many, ``allocated`` is how many have been given it so far. When
``allocated`` reaches ``capacity`` the row flips to ``exhausted`` and is no
longer picked for new orders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import CredentialStatus

if TYPE_CHECKING:
    pass


class ProductCredential(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "product_credentials"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("allocated >= 0", name="allocated_non_negative"),
        CheckConstraint("allocated <= capacity", name="allocated_within_capacity"),
        Index(
            "ix_product_credentials_business_id_product_id_status",
            "business_id",
            "product_id",
            "status",
        ),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Optional human label, e.g. "Steam acct #3".
    label: Mapped[str | None] = mapped_column(String(255))

    # The login id / email the customer signs in with. Not the secret, so kept
    # in cleartext for operational visibility; the password below is encrypted.
    username: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fernet-encrypted password/token. Never store the plaintext here.
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # How many customers may share this one account, and how many already do.
    capacity: Mapped[int] = mapped_column(nullable=False, server_default="1")
    allocated: Mapped[int] = mapped_column(nullable=False, server_default="0")

    status: Mapped[CredentialStatus] = mapped_column(
        pg_enum(CredentialStatus, "credential_status"),
        nullable=False,
        server_default=CredentialStatus.ACTIVE.value,
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    @property
    def has_free_slot(self) -> bool:
        return self.status is CredentialStatus.ACTIVE and self.allocated < self.capacity
