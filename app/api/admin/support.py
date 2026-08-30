"""Admin API — support ticket management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import NotFoundError
from app.core.uow import UnitOfWork
from app.models.business import Business
from app.models.enums import TicketPriority, TicketStatus
from app.models.support_ticket import SupportTicket
from app.repositories.support_tickets import SupportTicketRepository
from app.services.support_service import SupportService

router = APIRouter(prefix="/{slug}/support", tags=["admin:support"])


# -- schemas ------------------------------------------------------------------

class TicketOut(BaseModel):
    id: str
    reference: str
    status: str
    priority: str
    reason: str
    summary: str | None
    assigned_to: str | None
    customer_id: str

    @classmethod
    def from_orm(cls, t: SupportTicket) -> "TicketOut":
        return cls(
            id=str(t.id),
            reference=t.reference,
            status=t.status.value,
            priority=t.priority.value,
            reason=t.reason,
            summary=t.summary,
            assigned_to=t.assigned_to,
            customer_id=str(t.customer_id),
        )


class UpdateTicketIn(BaseModel):
    assigned_to: str | None = None
    status: TicketStatus | None = None
    resolution: str | None = None


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[TicketOut])
async def list_open_tickets(
    slug: str,
    priority: TicketPriority | None = None,
    limit: int = 100,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[TicketOut]:
    repo = SupportTicketRepository(session, business.id)
    tickets = await repo.list_open(priority=priority, limit=limit)
    return [TicketOut.from_orm(t) for t in tickets]


@router.get("/{reference}", response_model=TicketOut)
async def get_ticket(
    slug: str,
    reference: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> TicketOut:
    repo = SupportTicketRepository(session, business.id)
    ticket = await repo.get_by_reference(reference.upper())
    if ticket is None:
        raise NotFoundError("Support ticket not found.", details={"reference": reference})
    return TicketOut.from_orm(ticket)


@router.patch("/{reference}", response_model=TicketOut)
async def update_ticket(
    slug: str,
    reference: str,
    body: UpdateTicketIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> TicketOut:
    repo = SupportTicketRepository(session, business.id)
    ticket = await repo.get_by_reference(reference.upper())
    if ticket is None:
        raise NotFoundError("Support ticket not found.", details={"reference": reference})

    svc = SupportService(session, business.id)
    async with UnitOfWork(session):
        if body.assigned_to is not None:
            await svc.assign(ticket, agent=body.assigned_to)
        if body.status is TicketStatus.RESOLVED:
            await svc.resolve(ticket, resolution=body.resolution)
        elif body.status is not None:
            ticket.status = body.status

    return TicketOut.from_orm(ticket)
