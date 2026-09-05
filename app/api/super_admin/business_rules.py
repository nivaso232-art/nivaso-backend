"""Super-admin API — AI Playbook business rules management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import NotFoundError, ValidationError
from app.core.uow import UnitOfWork
from app.repositories.business_rules import BusinessRuleRepository

router = APIRouter(prefix="/playbook", tags=["super-admin:playbook"])

_VALID_SCOPES = {"global", "plan", "business"}
_VALID_PLANS = {"free", "starter", "pro", "enterprise"}


class BusinessRuleOut(BaseModel):
    id: str
    scope: str
    plan: str | None
    business_id: str | None
    trigger: str
    instruction: str
    feature_condition: str | None
    priority: int
    is_active: bool
    updated_by: str
    created_at: str
    updated_at: str


class BusinessRuleIn(BaseModel):
    scope: str
    plan: str | None = None
    business_id: str | None = None
    trigger: str
    instruction: str
    feature_condition: str | None = None
    priority: int = 50
    is_active: bool = True


class BusinessRulePatch(BaseModel):
    trigger: str | None = None
    instruction: str | None = None
    feature_condition: str | None = None
    priority: int | None = None
    is_active: bool | None = None
    scope: str | None = None
    plan: str | None = None
    business_id: str | None = None


def _out(rule: Any) -> BusinessRuleOut:
    return BusinessRuleOut(
        id=str(rule.id),
        scope=rule.scope,
        plan=rule.plan,
        business_id=str(rule.business_id) if rule.business_id else None,
        trigger=rule.trigger,
        instruction=rule.instruction,
        feature_condition=rule.feature_condition,
        priority=rule.priority,
        is_active=rule.is_active,
        updated_by=rule.updated_by,
        created_at=rule.created_at.isoformat(),
        updated_at=rule.updated_at.isoformat(),
    )


@router.get("", response_model=list[BusinessRuleOut])
async def list_rules(
    session: AsyncSession = Depends(get_session),
) -> list[BusinessRuleOut]:
    """Return all playbook rules ordered by scope, priority, trigger."""
    try:
        rules = await BusinessRuleRepository(session).list_all()
        return [_out(r) for r in rules]
    except Exception:
        return []


@router.post("", response_model=BusinessRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: BusinessRuleIn,
    session: AsyncSession = Depends(get_session),
) -> BusinessRuleOut:
    """Create a new playbook rule."""
    if body.scope not in _VALID_SCOPES:
        raise ValidationError(f"scope must be one of {sorted(_VALID_SCOPES)}")
    if body.scope == "plan" and body.plan not in _VALID_PLANS:
        raise ValidationError(f"plan must be one of {sorted(_VALID_PLANS)}")
    if body.scope == "business" and not body.business_id:
        raise ValidationError("business_id is required when scope='business'")

    data: dict = {
        "id": uuid.uuid4(),
        "scope": body.scope,
        "plan": body.plan,
        "business_id": uuid.UUID(body.business_id) if body.business_id else None,
        "trigger": body.trigger.strip(),
        "instruction": body.instruction.strip(),
        "feature_condition": body.feature_condition,
        "priority": body.priority,
        "is_active": body.is_active,
        "updated_by": "super-admin",
    }
    async with UnitOfWork(session):
        rule = await BusinessRuleRepository(session).create(data)
    return _out(rule)


@router.patch("/{rule_id}", response_model=BusinessRuleOut)
async def update_rule(
    rule_id: str,
    body: BusinessRulePatch,
    session: AsyncSession = Depends(get_session),
) -> BusinessRuleOut:
    """Update fields on an existing rule."""
    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise NotFoundError("Rule not found.")

    rule = await BusinessRuleRepository(session).get(rid)
    if rule is None:
        raise NotFoundError("Rule not found.")

    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k == "feature_condition"}
    if "trigger" in updates:
        updates["trigger"] = updates["trigger"].strip()
    if "instruction" in updates:
        updates["instruction"] = updates["instruction"].strip()
    if "business_id" in updates and updates["business_id"]:
        updates["business_id"] = uuid.UUID(updates["business_id"])
    updates["updated_by"] = "super-admin"

    async with UnitOfWork(session):
        await BusinessRuleRepository(session).update(rule, updates)
        await session.refresh(rule)
    return _out(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a playbook rule."""
    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise NotFoundError("Rule not found.")

    rule = await BusinessRuleRepository(session).get(rid)
    if rule is None:
        raise NotFoundError("Rule not found.")

    async with UnitOfWork(session):
        await BusinessRuleRepository(session).delete(rule)
