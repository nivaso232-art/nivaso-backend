"""Credential vault operations: stock a pool, allocate a slot, reveal a secret.

Encryption happens here at the boundary — callers deal in plaintext, the DB
only ever sees ciphertext (``app/core/crypto.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models.credential import ProductCredential
from app.models.enums import CredentialStatus
from app.repositories.credentials import CredentialRepository

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AllocatedCredential:
    """A decrypted credential, held only long enough to hand to the customer."""

    credential_id: uuid.UUID
    username: str
    password: str


class CredentialService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.credentials = CredentialRepository(session, business_id)

    async def add_credential(
        self,
        *,
        product_id: uuid.UUID,
        username: str,
        password: str,
        capacity: int = 1,
        label: str | None = None,
    ) -> ProductCredential:
        credential = ProductCredential(
            product_id=product_id,
            username=username,
            secret_encrypted=crypto.encrypt(password),
            capacity=capacity,
            label=label,
            status=CredentialStatus.ACTIVE,
        )
        await self.credentials.add(credential)
        log.info(
            "credential_added",
            credential_id=str(credential.id),
            product_id=str(product_id),
            capacity=capacity,
        )
        return credential

    async def free_slots(self, product_id: uuid.UUID) -> int:
        return await self.credentials.count_free_slots(product_id)

    async def allocate(self, product_id: uuid.UUID) -> AllocatedCredential | None:
        """Take one slot from the pool and return the decrypted login, or None."""
        credential = await self.credentials.acquire_free_slot(product_id)
        if credential is None:
            return None

        credential.allocated += 1
        if credential.allocated >= credential.capacity:
            credential.status = CredentialStatus.EXHAUSTED
        await self.session.flush()

        return AllocatedCredential(
            credential_id=credential.id,
            username=credential.username,
            password=crypto.decrypt(credential.secret_encrypted),
        )

    async def reveal(self, credential_id: uuid.UUID) -> AllocatedCredential | None:
        """Re-read an already-allocated credential (for re-delivery)."""
        credential = await self.credentials.get(credential_id)
        if credential is None:
            return None
        return AllocatedCredential(
            credential_id=credential.id,
            username=credential.username,
            password=crypto.decrypt(credential.secret_encrypted),
        )

    async def list_for_product(
        self, product_id: uuid.UUID
    ) -> Sequence[ProductCredential]:
        return await self.credentials.list_for_product(product_id)
