"""Admin API — coupon catalog management."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ConflictError, ForbiddenError, ValidationError
from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import check, resolve
from app.models.business import Business
from app.models.coupon import Coupon, CouponDiscountType, CouponStatus
from app.models.field_definition import FieldEntityType
from app.repositories.coupons import CouponRepository
from app.repositories.entitlements import EntitlementRepository
from app.repositories.field_definitions import FieldDefinitionRepository
from app.services.custom_fields import validate_custom_fields

router = APIRouter(prefix="/{slug}/coupons", tags=["admin:coupons"])


# -- schemas ------------------------------------------------------------------

class CouponOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    discount_type: str
    discount_value: str
    max_uses: int | None
    used_count: int
    min_order_amount: str | None
    starts_at: str | None
    ends_at: str | None
    status: str
    custom_fields: dict[str, Any]

    @classmethod
    def from_orm(cls, c: Coupon) -> "CouponOut":
        return cls(
            id=str(c.id),
            code=c.code,
            discount_type=c.discount_type.value,
            discount_value=str(c.discount_value),
            max_uses=c.max_uses,
            used_count=c.used_count,
            min_order_amount=str(c.min_order_amount) if c.min_order_amount is not None else None,
            starts_at=c.starts_at.isoformat() if c.starts_at is not None else None,
            ends_at=c.ends_at.isoformat() if c.ends_at is not None else None,
            status=c.status.value,
            custom_fields=c.custom_fields,
        )


class CreateCouponIn(BaseModel):
    code: str
    discount_type: CouponDiscountType
    discount_value: Decimal
    max_uses: int | None = None
    min_order_amount: Decimal | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: CouponStatus = CouponStatus.ACTIVE
    custom_fields: dict[str, Any] = {}


class UpdateCouponIn(BaseModel):
    code: str | None = None
    discount_type: CouponDiscountType | None = None
    discount_value: Decimal | None = None
    max_uses: int | None = None
    min_order_amount: Decimal | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: CouponStatus | None = None
    custom_fields: dict[str, Any] | None = None


def _parse_uuid(coupon_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(coupon_id)
    except ValueError:
        raise ValidationError(
            "coupon_id must be a valid UUID.", details={"coupon_id": coupon_id}
        )


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[CouponOut])
async def list_coupons(
    slug: str,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[CouponOut]:
    repo = CouponRepository(session, business.id)
    coupons = await repo.list(limit=limit, offset=offset, order_by=Coupon.created_at)
    return [CouponOut.from_orm(c) for c in coupons]


@router.post("", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
async def create_coupon(
    slug: str,
    body: CreateCouponIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> CouponOut:
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(business.id)
    if not check(resolve(ent.plan, ent.overrides), FeatureFlag.MODULE_COUPONS):
        raise ForbiddenError(
            "Coupons are not enabled on your plan.",
            details={"flag": FeatureFlag.MODULE_COUPONS},
        )

    repo = CouponRepository(session, business.id)
    existing = await repo.get_by_code(body.code)
    if existing is not None:
        raise ConflictError(
            f"A coupon with code '{body.code}' already exists.",
            details={"code": body.code},
        )

    field_defs = await FieldDefinitionRepository(session, business.id).list_by_entity_type(
        FieldEntityType.COUPON
    )
    cleaned_custom_fields = await validate_custom_fields(field_defs, body.custom_fields)

    coupon = Coupon(
        code=body.code,
        discount_type=body.discount_type,
        discount_value=body.discount_value,
        max_uses=body.max_uses,
        min_order_amount=body.min_order_amount,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=body.status,
        custom_fields=cleaned_custom_fields,
    )
    async with UnitOfWork(session):
        await repo.add(coupon)
    return CouponOut.from_orm(coupon)


@router.get("/{coupon_id}", response_model=CouponOut)
async def get_coupon(
    slug: str,
    coupon_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> CouponOut:
    cid = _parse_uuid(coupon_id)
    repo = CouponRepository(session, business.id)
    coupon = await repo.get_or_raise(cid)
    return CouponOut.from_orm(coupon)


@router.patch("/{coupon_id}", response_model=CouponOut)
async def update_coupon(
    slug: str,
    coupon_id: str,
    body: UpdateCouponIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> CouponOut:
    cid = _parse_uuid(coupon_id)
    repo = CouponRepository(session, business.id)
    coupon = await repo.get_or_raise(cid)

    if body.code is not None and body.code != coupon.code:
        existing = await repo.get_by_code(body.code)
        if existing is not None and existing.id != coupon.id:
            raise ConflictError(
                f"A coupon with code '{body.code}' already exists.",
                details={"code": body.code},
            )

    cleaned_custom_fields: dict[str, Any] | None = None
    if body.custom_fields is not None:
        field_defs = await FieldDefinitionRepository(
            session, business.id
        ).list_by_entity_type(FieldEntityType.COUPON)
        cleaned_custom_fields = await validate_custom_fields(field_defs, body.custom_fields)

    async with UnitOfWork(session):
        if body.code is not None:
            coupon.code = body.code
        if body.discount_type is not None:
            coupon.discount_type = body.discount_type
        if body.discount_value is not None:
            coupon.discount_value = body.discount_value
        if body.max_uses is not None:
            coupon.max_uses = body.max_uses
        if body.min_order_amount is not None:
            coupon.min_order_amount = body.min_order_amount
        if body.starts_at is not None:
            coupon.starts_at = body.starts_at
        if body.ends_at is not None:
            coupon.ends_at = body.ends_at
        if body.status is not None:
            coupon.status = body.status
        if cleaned_custom_fields is not None:
            coupon.custom_fields = cleaned_custom_fields

    return CouponOut.from_orm(coupon)


@router.delete("/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_coupon(
    slug: str,
    coupon_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Disable a coupon (soft delete). Order history referencing it is preserved."""
    cid = _parse_uuid(coupon_id)
    repo = CouponRepository(session, business.id)
    coupon = await repo.get_or_raise(cid)
    async with UnitOfWork(session):
        coupon.status = CouponStatus.DISABLED
