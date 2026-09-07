"""Admin API — per-business connector automation flows.

Routes under /{slug}/automations:
  GET    /              — list all automation configs for this business
  GET    /modules       — return the module catalog for the flow builder
  GET    /{connector}   — get one automation config
  PUT    /{connector}   — upsert (name + flow)
  PATCH  /{connector}/activate  — toggle is_active
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.channels.automation.modules import AUTOMATION_MODULE_CATALOG
from app.core.errors import NotFoundError
from app.core.uow import UnitOfWork
from app.models.business import Business
from app.models.connector_automation import ConnectorAutomation
from app.repositories.connector_automations import ConnectorAutomationRepository

router = APIRouter(prefix="/{slug}/automations", tags=["admin:automations"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_DEFAULT_FLOW: dict[str, Any] = {
    "welcome_message": "",
    "fallback_message": "Sorry, I didn't understand. Please choose an option.",
    "nodes": [],
}


class AutomationOut(BaseModel):
    connector_type: str
    name: str
    is_active: bool
    flow: dict[str, Any]

    @classmethod
    def from_orm(cls, row: ConnectorAutomation) -> "AutomationOut":
        return cls(
            connector_type=row.connector_type,
            name=row.name,
            is_active=row.is_active,
            flow=row.flow,
        )


class AutomationIn(BaseModel):
    name: str = "Default Flow"
    flow: dict[str, Any] = _DEFAULT_FLOW  # type: ignore[assignment]


class ActivateIn(BaseModel):
    is_active: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AutomationOut])
async def list_automations(
    slug: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[AutomationOut]:
    """List all automation configs for this business."""
    repo = ConnectorAutomationRepository(session)
    rows = await repo.list_for_business(business.id)
    return [AutomationOut.from_orm(r) for r in rows]


@router.get("/modules", response_model=dict[str, Any])
async def get_module_catalog(
    slug: str,
    _business: Business = Depends(get_business),
) -> dict[str, Any]:
    """Return the module catalog for the frontend flow builder.

    The ``slug`` path parameter is required by the router prefix pattern but
    is only used to enforce that the caller has access to the business (via
    ``get_business`` dependency). The catalog itself is global.
    """
    return AUTOMATION_MODULE_CATALOG


@router.get("/{connector_type}", response_model=AutomationOut)
async def get_automation(
    slug: str,
    connector_type: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> AutomationOut:
    """Get one automation config. 404 if not yet configured."""
    repo = ConnectorAutomationRepository(session)
    row = await repo.get_for_business(business.id, connector_type)
    if row is None:
        raise NotFoundError(
            f"No automation configured for connector '{connector_type}'.",
            details={"connector_type": connector_type},
        )
    return AutomationOut.from_orm(row)


@router.put(
    "/{connector_type}",
    response_model=AutomationOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_automation(
    slug: str,
    connector_type: str,
    body: AutomationIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> AutomationOut:
    """Create or update an automation flow. Does not change is_active."""
    repo = ConnectorAutomationRepository(session)
    async with UnitOfWork(session):
        row = await repo.upsert(
            business_id=business.id,
            connector_type=connector_type,
            name=body.name,
            flow=body.flow,
        )
    return AutomationOut.from_orm(row)


@router.patch(
    "/{connector_type}/activate",
    response_model=AutomationOut,
    status_code=status.HTTP_200_OK,
)
async def toggle_automation(
    slug: str,
    connector_type: str,
    body: ActivateIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> AutomationOut:
    """Activate or deactivate an automation flow."""
    repo = ConnectorAutomationRepository(session)
    async with UnitOfWork(session):
        row = await repo.set_active(business.id, connector_type, body.is_active)
    if row is None:
        raise NotFoundError(
            f"No automation configured for connector '{connector_type}'.",
            details={"connector_type": connector_type},
        )
    return AutomationOut.from_orm(row)
