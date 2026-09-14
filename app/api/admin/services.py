"""Admin API — service catalog management.

Mirrors ``app/api/admin/products.py``'s structure. Services are gated behind
``FeatureFlag.MODULE_SERVICES`` (entitlement check on create), and their
``custom_fields`` values are validated against the admin-defined
``FieldDefinition`` rows for ``FieldEntityType.SERVICE``
(see ``app/services/custom_fields.py``).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ForbiddenError, ValidationError
from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import check, resolve
from app.models.business import Business
from app.models.field_definition import FieldEntityType
from app.models.service import Service, ServiceStatus
from app.repositories.entitlements import EntitlementRepository
from app.repositories.field_definitions import FieldDefinitionRepository
from app.repositories.services import ServiceRepository
from app.services.custom_fields import validate_custom_fields

router = APIRouter(prefix="/{slug}/services", tags=["admin:services"])


# -- schemas ------------------------------------------------------------------

class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    price: str
    currency: str
    duration_minutes: int | None
    category: str | None
    status: str
    custom_fields: dict[str, Any]

    @classmethod
    def from_orm(cls, s: Service) -> "ServiceOut":
        return cls(
            id=str(s.id),
            name=s.name,
            description=s.description,
            price=str(s.price),
            currency=s.currency,
            duration_minutes=s.duration_minutes,
            category=s.category,
            status=s.status.value,
            custom_fields=s.custom_fields,
        )


class CreateServiceIn(BaseModel):
    name: str
    description: str | None = None
    price: Decimal
    currency: str = "INR"
    duration_minutes: int | None = None
    category: str | None = None
    status: ServiceStatus = ServiceStatus.ACTIVE
    custom_fields: dict[str, Any] = {}


class UpdateServiceIn(BaseModel):
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    duration_minutes: int | None = None
    category: str | None = None
    status: ServiceStatus | None = None
    custom_fields: dict[str, Any] | None = None


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationError(
            f"{field_name} must be a valid UUID.", details={field_name: raw}
        )


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[ServiceOut])
async def list_services(
    slug: str,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[ServiceOut]:
    repo = ServiceRepository(session, business.id)
    services = await repo.list_active(category=category, limit=limit, offset=offset)
    return [ServiceOut.from_orm(s) for s in services]


@router.post("", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(
    slug: str,
    body: CreateServiceIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ServiceOut:
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(business.id)
    if not check(resolve(ent.plan, ent.overrides), FeatureFlag.MODULE_SERVICES):
        raise ForbiddenError("The Services module is not enabled for this business.")

    field_repo = FieldDefinitionRepository(session, business.id)
    definitions = await field_repo.list_by_entity_type(FieldEntityType.SERVICE)
    cleaned_custom_fields = await validate_custom_fields(definitions, body.custom_fields)

    service = Service(
        name=body.name,
        description=body.description,
        price=body.price,
        currency=body.currency,
        duration_minutes=body.duration_minutes,
        category=body.category,
        status=body.status,
        custom_fields=cleaned_custom_fields,
    )
    async with UnitOfWork(session):
        repo = ServiceRepository(session, business.id)
        await repo.add(service)
    return ServiceOut.from_orm(service)


@router.get("/{service_id}", response_model=ServiceOut)
async def get_service(
    slug: str,
    service_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ServiceOut:
    sid = _parse_uuid(service_id, "service_id")
    repo = ServiceRepository(session, business.id)
    service = await repo.get_or_raise(sid)
    return ServiceOut.from_orm(service)


@router.patch("/{service_id}", response_model=ServiceOut)
async def update_service(
    slug: str,
    service_id: str,
    body: UpdateServiceIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ServiceOut:
    sid = _parse_uuid(service_id, "service_id")
    repo = ServiceRepository(session, business.id)
    service = await repo.get_or_raise(sid)

    cleaned_custom_fields: dict[str, Any] | None = None
    if body.custom_fields is not None:
        field_repo = FieldDefinitionRepository(session, business.id)
        definitions = await field_repo.list_by_entity_type(FieldEntityType.SERVICE)
        cleaned_custom_fields = await validate_custom_fields(definitions, body.custom_fields)

    async with UnitOfWork(session):
        if body.name is not None:
            service.name = body.name
        if body.description is not None:
            service.description = body.description
        if body.price is not None:
            service.price = body.price
        if body.currency is not None:
            service.currency = body.currency
        if body.duration_minutes is not None:
            service.duration_minutes = body.duration_minutes
        if body.category is not None:
            service.category = body.category
        if body.status is not None:
            service.status = body.status
        if cleaned_custom_fields is not None:
            service.custom_fields = cleaned_custom_fields

    return ServiceOut.from_orm(service)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_service(
    slug: str,
    service_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Archive a service (soft delete)."""
    sid = _parse_uuid(service_id, "service_id")
    repo = ServiceRepository(session, business.id)
    service = await repo.get_or_raise(sid)
    async with UnitOfWork(session):
        service.status = ServiceStatus.ARCHIVED
