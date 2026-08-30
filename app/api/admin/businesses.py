"""Admin API — business management.

All routes require the X-Internal-Key header (enforced at the router level
in ``main.py``). No customer-facing auth is used here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.uow import UnitOfWork
from app.models.business import Business
from app.models.enums import BusinessStatus
from app.core.errors import NotFoundError
from app.repositories.businesses import BusinessRepository

router = APIRouter(prefix="/businesses", tags=["admin:businesses"])


# -- schemas ------------------------------------------------------------------

class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str | None
    timezone: str
    status: str
    settings: dict[str, Any]

    @classmethod
    def from_orm(cls, b: Business) -> "BusinessOut":
        return cls(
            id=str(b.id),
            slug=b.slug,
            name=b.name,
            description=b.description,
            timezone=b.timezone,
            status=b.status.value,
            settings=b.settings,
        )


class CreateBusinessIn(BaseModel):
    slug: str
    name: str
    description: str | None = None
    timezone: str = "Asia/Kolkata"
    settings: dict[str, Any] = {}


class UpdateBusinessIn(BaseModel):
    name: str | None = None
    description: str | None = None
    timezone: str | None = None
    status: BusinessStatus | None = None
    settings: dict[str, Any] | None = None


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[BusinessOut])
async def list_businesses(
    session: AsyncSession = Depends(get_session),
) -> list[BusinessOut]:
    repo = BusinessRepository(session)
    businesses = await repo.list_all()
    return [BusinessOut.from_orm(b) for b in businesses]


@router.post("", response_model=BusinessOut, status_code=status.HTTP_201_CREATED)
async def create_business(
    body: CreateBusinessIn,
    session: AsyncSession = Depends(get_session),
) -> BusinessOut:
    business = Business(
        slug=body.slug,
        name=body.name,
        description=body.description,
        timezone=body.timezone,
        status=BusinessStatus.ACTIVE,
        settings=body.settings,
    )
    async with UnitOfWork(session):
        repo = BusinessRepository(session)
        await repo.add(business)
    return BusinessOut.from_orm(business)


@router.get("/{slug}", response_model=BusinessOut)
async def get_business(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> BusinessOut:
    repo = BusinessRepository(session)
    business = await repo.get_by_slug_or_raise(slug)
    return BusinessOut.from_orm(business)


@router.patch("/{slug}", response_model=BusinessOut)
async def update_business(
    slug: str,
    body: UpdateBusinessIn,
    session: AsyncSession = Depends(get_session),
) -> BusinessOut:
    repo = BusinessRepository(session)
    business = await repo.get_by_slug_or_raise(slug)

    async with UnitOfWork(session):
        if body.name is not None:
            business.name = body.name
        if body.description is not None:
            business.description = body.description
        if body.timezone is not None:
            business.timezone = body.timezone
        if body.status is not None:
            business.status = body.status
        if body.settings is not None:
            business.settings = body.settings

    return BusinessOut.from_orm(business)
