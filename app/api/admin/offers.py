"""Admin API — offer (discount campaign) management."""

from __future__ import annotations

import uuid
from datetime import datetime
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
from app.models.offer import Offer, OfferDiscountType, OfferStatus
from app.repositories.entitlements import EntitlementRepository
from app.repositories.field_definitions import FieldDefinitionRepository
from app.repositories.offers import OfferRepository
from app.services.custom_fields import validate_custom_fields

router = APIRouter(prefix="/{slug}/offers", tags=["admin:offers"])


# -- schemas ------------------------------------------------------------------

class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    discount_type: str
    discount_value: str
    applies_to: dict[str, Any]
    starts_at: str | None
    ends_at: str | None
    status: str
    custom_fields: dict[str, Any]

    @classmethod
    def from_orm(cls, o: Offer) -> "OfferOut":
        return cls(
            id=str(o.id),
            name=o.name,
            description=o.description,
            discount_type=o.discount_type.value,
            discount_value=str(o.discount_value),
            applies_to=o.applies_to,
            starts_at=o.starts_at.isoformat() if o.starts_at else None,
            ends_at=o.ends_at.isoformat() if o.ends_at else None,
            status=o.status.value,
            custom_fields=o.custom_fields,
        )


class CreateOfferIn(BaseModel):
    name: str
    description: str | None = None
    discount_type: OfferDiscountType
    discount_value: Decimal
    applies_to: dict[str, Any] = {}
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: OfferStatus = OfferStatus.DRAFT
    custom_fields: dict[str, Any] = {}


class UpdateOfferIn(BaseModel):
    name: str | None = None
    description: str | None = None
    discount_type: OfferDiscountType | None = None
    discount_value: Decimal | None = None
    applies_to: dict[str, Any] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: OfferStatus | None = None
    custom_fields: dict[str, Any] | None = None


def _parse_uuid(raw: str, *, field: str = "offer_id") -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationError(f"{field} must be a valid UUID.", details={field: raw})


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[OfferOut])
async def list_offers(
    slug: str,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[OfferOut]:
    repo = OfferRepository(session, business.id)
    offers = await repo.list_active(limit=limit, offset=offset)
    return [OfferOut.from_orm(o) for o in offers]


@router.post("", response_model=OfferOut, status_code=status.HTTP_201_CREATED)
async def create_offer(
    slug: str,
    body: CreateOfferIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> OfferOut:
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(business.id)
    if not check(resolve(ent.plan, ent.overrides), FeatureFlag.MODULE_OFFERS):
        raise ForbiddenError(
            "Offers are not enabled on your plan.",
            details={"flag": FeatureFlag.MODULE_OFFERS},
        )

    field_defs = await FieldDefinitionRepository(session, business.id).list_by_entity_type(
        FieldEntityType.OFFER
    )
    cleaned_custom_fields = await validate_custom_fields(field_defs, body.custom_fields)

    offer = Offer(
        name=body.name,
        description=body.description,
        discount_type=body.discount_type,
        discount_value=body.discount_value,
        applies_to=body.applies_to,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=body.status,
        custom_fields=cleaned_custom_fields,
    )
    async with UnitOfWork(session):
        repo = OfferRepository(session, business.id)
        await repo.add(offer)
    return OfferOut.from_orm(offer)


@router.get("/{offer_id}", response_model=OfferOut)
async def get_offer(
    slug: str,
    offer_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> OfferOut:
    oid = _parse_uuid(offer_id)
    repo = OfferRepository(session, business.id)
    offer = await repo.get_or_raise(oid)
    return OfferOut.from_orm(offer)


@router.patch("/{offer_id}", response_model=OfferOut)
async def update_offer(
    slug: str,
    offer_id: str,
    body: UpdateOfferIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> OfferOut:
    oid = _parse_uuid(offer_id)
    repo = OfferRepository(session, business.id)
    offer = await repo.get_or_raise(oid)

    cleaned_custom_fields: dict[str, Any] | None = None
    if body.custom_fields is not None:
        field_defs = await FieldDefinitionRepository(
            session, business.id
        ).list_by_entity_type(FieldEntityType.OFFER)
        cleaned_custom_fields = await validate_custom_fields(field_defs, body.custom_fields)

    async with UnitOfWork(session):
        if body.name is not None:
            offer.name = body.name
        if body.description is not None:
            offer.description = body.description
        if body.discount_type is not None:
            offer.discount_type = body.discount_type
        if body.discount_value is not None:
            offer.discount_value = body.discount_value
        if body.applies_to is not None:
            offer.applies_to = body.applies_to
        if body.starts_at is not None:
            offer.starts_at = body.starts_at
        if body.ends_at is not None:
            offer.ends_at = body.ends_at
        if body.status is not None:
            offer.status = body.status
        if cleaned_custom_fields is not None:
            offer.custom_fields = cleaned_custom_fields

    return OfferOut.from_orm(offer)


@router.delete("/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_offer(
    slug: str,
    offer_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Archive an offer (soft delete)."""
    oid = _parse_uuid(offer_id)
    repo = OfferRepository(session, business.id)
    offer = await repo.get_or_raise(oid)
    async with UnitOfWork(session):
        offer.status = OfferStatus.ARCHIVED
