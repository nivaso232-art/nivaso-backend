"""Admin API — appointment scheduling.

Appointment is a static, fixed-schema entity (like Order/Customer/
SupportTicket) — it does NOT use the admin-defined custom-fields system, so
unlike products/services/offers/coupons there is no ``attributes``/
``custom_fields`` bag exposed here beyond the generic ``metadata_``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ForbiddenError, ValidationError
from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import check, resolve
from app.models.appointment import Appointment, AppointmentStatus
from app.models.business import Business
from app.repositories.appointments import AppointmentRepository
from app.repositories.customers import CustomerRepository
from app.repositories.entitlements import EntitlementRepository
from app.repositories.services import ServiceRepository

router = APIRouter(prefix="/{slug}/appointments", tags=["admin:appointments"])


# -- schemas ------------------------------------------------------------------

class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    customer_name: str | None
    service_id: str | None
    service_name: str | None
    scheduled_at: str
    duration_minutes: int
    status: str
    notes: str | None

    @classmethod
    def build(
        cls,
        a: Appointment,
        *,
        customer_name: str | None,
        service_name: str | None,
    ) -> "AppointmentOut":
        return cls(
            id=str(a.id),
            customer_id=str(a.customer_id),
            customer_name=customer_name,
            service_id=str(a.service_id) if a.service_id else None,
            service_name=service_name,
            scheduled_at=a.scheduled_at.isoformat(),
            duration_minutes=a.duration_minutes,
            status=a.status.value,
            notes=a.notes,
        )


class CreateAppointmentIn(BaseModel):
    customer_id: str
    service_id: str | None = None
    scheduled_at: datetime
    duration_minutes: int = 30
    status: AppointmentStatus = AppointmentStatus.SCHEDULED
    notes: str | None = None


class UpdateAppointmentIn(BaseModel):
    customer_id: str | None = None
    service_id: str | None = None
    scheduled_at: datetime | None = None
    duration_minutes: int | None = None
    status: AppointmentStatus | None = None
    notes: str | None = None


# -- helpers ------------------------------------------------------------------

def _parse_uuid(raw: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationError(f"{field} must be a valid UUID.", details={field: raw})


async def _to_out(
    appointment: Appointment, session: AsyncSession, business_id: uuid.UUID
) -> AppointmentOut:
    customer_name: str | None = None
    service_name: str | None = None

    customer = await CustomerRepository(session, business_id).get(appointment.customer_id)
    if customer is not None:
        customer_name = customer.display_name

    if appointment.service_id is not None:
        service = await ServiceRepository(session, business_id).get(appointment.service_id)
        if service is not None:
            service_name = service.name

    return AppointmentOut.build(
        appointment, customer_name=customer_name, service_name=service_name
    )


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[AppointmentOut])
async def list_appointments(
    slug: str,
    limit: int = 50,
    offset: int = 0,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[AppointmentOut]:
    repo = AppointmentRepository(session, business.id)
    appointments = await repo.list_upcoming(limit=limit, offset=offset)
    return [await _to_out(a, session, business.id) for a in appointments]


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    slug: str,
    body: CreateAppointmentIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(business.id)
    if not check(resolve(ent.plan, ent.overrides), FeatureFlag.MODULE_APPOINTMENTS):
        raise ForbiddenError(
            "Appointments are not enabled on your plan.",
            details={"flag": FeatureFlag.MODULE_APPOINTMENTS},
        )

    customer_id = _parse_uuid(body.customer_id, "customer_id")
    service_id = _parse_uuid(body.service_id, "service_id") if body.service_id else None

    appointment = Appointment(
        customer_id=customer_id,
        service_id=service_id,
        scheduled_at=body.scheduled_at,
        duration_minutes=body.duration_minutes,
        status=body.status,
        notes=body.notes,
    )
    async with UnitOfWork(session):
        repo = AppointmentRepository(session, business.id)
        await repo.add(appointment)

    return await _to_out(appointment, session, business.id)


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    slug: str,
    appointment_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    aid = _parse_uuid(appointment_id, "appointment_id")
    repo = AppointmentRepository(session, business.id)
    appointment = await repo.get_or_raise(aid)
    return await _to_out(appointment, session, business.id)


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def update_appointment(
    slug: str,
    appointment_id: str,
    body: UpdateAppointmentIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    aid = _parse_uuid(appointment_id, "appointment_id")
    repo = AppointmentRepository(session, business.id)
    appointment = await repo.get_or_raise(aid)

    async with UnitOfWork(session):
        if body.customer_id is not None:
            appointment.customer_id = _parse_uuid(body.customer_id, "customer_id")
        if body.service_id is not None:
            appointment.service_id = _parse_uuid(body.service_id, "service_id")
        if body.scheduled_at is not None:
            appointment.scheduled_at = body.scheduled_at
        if body.duration_minutes is not None:
            appointment.duration_minutes = body.duration_minutes
        if body.status is not None:
            appointment.status = body.status
        if body.notes is not None:
            appointment.notes = body.notes

    return await _to_out(appointment, session, business.id)


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    slug: str,
    appointment_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Cancel an appointment (soft delete). History is preserved."""
    aid = _parse_uuid(appointment_id, "appointment_id")
    repo = AppointmentRepository(session, business.id)
    appointment = await repo.get_or_raise(aid)
    async with UnitOfWork(session):
        appointment.status = AppointmentStatus.CANCELLED
