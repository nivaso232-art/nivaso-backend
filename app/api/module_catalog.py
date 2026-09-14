"""Module catalog — public read endpoint.

Backs the unauthenticated signup page: it needs to know what modules and
integrations are requestable (``SignupIn.requested_modules`` in
``app/api/auth.py``) without a token. Deliberately unauthenticated, mirroring
``POST /auth/signup`` itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.module_catalog import ModuleCatalogRepository

router = APIRouter(prefix="/module-catalog", tags=["module-catalog"])


class ModuleCatalogOut(BaseModel):
    key: str
    category: str
    display_name: str
    description: str | None
    sort_order: int


@router.get("", response_model=list[ModuleCatalogOut])
async def list_module_catalog(
    session: AsyncSession = Depends(get_session),
) -> list[ModuleCatalogOut]:
    """Active catalog entries, ordered by category then sort_order."""
    repo = ModuleCatalogRepository(session)
    rows = await repo.list_active()
    return [
        ModuleCatalogOut(
            key=row.key,
            category=row.category.value,
            display_name=row.display_name,
            description=row.description,
            sort_order=row.sort_order,
        )
        for row in rows
    ]
