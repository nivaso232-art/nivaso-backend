"""Admin API — custom field definitions (per business, per entity type).

Gated by TWO flags at once: ``FeatureFlag.MODULE_CUSTOM_FIELDS`` (the general
"can this business manage field schemas at all" capability) AND the specific
entity type's own module flag (e.g. managing SERVICE fields also requires
``MODULE_SERVICES``). Having Custom Fields enabled does not by itself unlock
schema management for a module the business hasn't separately been granted.

This only controls the ability to *define* field schemas via this router —
Product/Service/Offer/Coupon still validate against whatever definitions
already exist regardless of these flags; it does not affect core catalog
behavior, only whether the business can manage the schemas themselves.
"""

from __future__ import annotations

import uuid
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
from app.models.field_definition import FieldDefinition, FieldEntityType, FieldType
from app.repositories.entitlements import EntitlementRepository
from app.repositories.field_definitions import FieldDefinitionRepository

router = APIRouter(prefix="/{slug}/custom-fields/{entity_type}", tags=["admin:custom-fields"])

# Managing an entity type's custom fields requires that entity type's own
# module to be enabled too — Custom Fields being on does not, by itself,
# unlock schema management for a module the business hasn't been granted.
_ENTITY_TYPE_MODULE_FLAG: dict[FieldEntityType, str] = {
    FieldEntityType.PRODUCT: FeatureFlag.MODULE_PRODUCTS,
    FieldEntityType.SERVICE: FeatureFlag.MODULE_SERVICES,
    FieldEntityType.OFFER: FeatureFlag.MODULE_OFFERS,
    FieldEntityType.COUPON: FeatureFlag.MODULE_COUPONS,
}


async def _require_custom_fields_module(
    business: Business, session: AsyncSession, entity_type: FieldEntityType
) -> None:
    ent = await EntitlementRepository(session).get_or_create(business.id)
    resolved = resolve(ent.plan, ent.overrides)
    if not check(resolved, FeatureFlag.MODULE_CUSTOM_FIELDS):
        raise ForbiddenError("The Custom Fields module is not enabled for this business.")
    entity_flag = _ENTITY_TYPE_MODULE_FLAG[entity_type]
    if not check(resolved, entity_flag):
        raise ForbiddenError(
            f"Enable the {entity_type.value.capitalize()} module before managing its custom fields.",
            details={"entity_type": entity_type.value, "required_flag": entity_flag},
        )


# -- schemas ------------------------------------------------------------------

class FieldDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: str
    key: str
    label: str
    field_type: str
    options: list[Any] | None
    required: bool
    sort_order: int

    @classmethod
    def from_orm(cls, f: FieldDefinition) -> "FieldDefinitionOut":
        return cls(
            id=str(f.id),
            entity_type=f.entity_type.value,
            key=f.key,
            label=f.label,
            field_type=f.field_type.value,
            options=f.options,
            required=f.required,
            sort_order=f.sort_order,
        )


class CreateFieldDefinitionIn(BaseModel):
    key: str
    label: str
    field_type: FieldType
    options: list[Any] | None = None
    required: bool = False
    sort_order: int | None = None


class UpdateFieldDefinitionIn(BaseModel):
    label: str | None = None
    field_type: FieldType | None = None
    options: list[Any] | None = None
    required: bool | None = None
    sort_order: int | None = None


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationError(
            f"{field_name} must be a valid UUID.", details={field_name: raw}
        )


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[FieldDefinitionOut])
async def list_field_definitions(
    slug: str,
    entity_type: FieldEntityType,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[FieldDefinitionOut]:
    await _require_custom_fields_module(business, session, entity_type)
    repo = FieldDefinitionRepository(session, business.id)
    definitions = await repo.list_by_entity_type(entity_type)
    return [FieldDefinitionOut.from_orm(d) for d in definitions]


@router.post("", response_model=FieldDefinitionOut, status_code=status.HTTP_201_CREATED)
async def create_field_definition(
    slug: str,
    entity_type: FieldEntityType,
    body: CreateFieldDefinitionIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> FieldDefinitionOut:
    await _require_custom_fields_module(business, session, entity_type)
    repo = FieldDefinitionRepository(session, business.id)

    existing = await repo.get_by_key(entity_type, body.key)
    if existing is not None:
        raise ValidationError(
            f"A field with key '{body.key}' already exists for {entity_type.value}.",
            details={"key": body.key, "entity_type": entity_type.value},
        )

    sort_order = body.sort_order
    if sort_order is None:
        current = await repo.list_by_entity_type(entity_type)
        sort_order = (max((d.sort_order for d in current), default=-1)) + 1

    definition = FieldDefinition(
        entity_type=entity_type,
        key=body.key,
        label=body.label,
        field_type=body.field_type,
        options=body.options,
        required=body.required,
        sort_order=sort_order,
    )
    async with UnitOfWork(session):
        await repo.add(definition)
    return FieldDefinitionOut.from_orm(definition)


@router.patch("/{field_id}", response_model=FieldDefinitionOut)
async def update_field_definition(
    slug: str,
    entity_type: FieldEntityType,
    field_id: str,
    body: UpdateFieldDefinitionIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> FieldDefinitionOut:
    await _require_custom_fields_module(business, session, entity_type)
    fid = _parse_uuid(field_id, "field_id")
    repo = FieldDefinitionRepository(session, business.id)
    definition = await repo.get_or_raise(fid)

    async with UnitOfWork(session):
        if body.label is not None:
            definition.label = body.label
        if body.field_type is not None:
            definition.field_type = body.field_type
        if body.options is not None:
            definition.options = body.options
        if body.required is not None:
            definition.required = body.required
        if body.sort_order is not None:
            definition.sort_order = body.sort_order

    return FieldDefinitionOut.from_orm(definition)


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field_definition(
    slug: str,
    entity_type: FieldEntityType,
    field_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard delete — these are schema definitions, not tenant data."""
    await _require_custom_fields_module(business, session, entity_type)
    fid = _parse_uuid(field_id, "field_id")
    repo = FieldDefinitionRepository(session, business.id)
    definition = await repo.get_or_raise(fid)
    async with UnitOfWork(session):
        await repo.delete(definition)
