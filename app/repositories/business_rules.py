"""Repository for AI Playbook business rules."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_rule import BusinessRule


def _eval_condition(condition: str | None, entitlements: dict[str, Any]) -> bool:
    """Evaluate a feature_condition string against resolved entitlements.

    Supported formats:
      "orders.enabled=false"   → True when orders.enabled is falsy
      "channel.payments=true"  → True when channel.payments is truthy
      null / empty             → always True (no condition)
    """
    if not condition or not condition.strip():
        return True
    condition = condition.strip()
    if "=false" in condition:
        flag = condition.replace("=false", "").strip()
        return not bool(entitlements.get(flag))
    if "=true" in condition:
        flag = condition.replace("=true", "").strip()
        return bool(entitlements.get(flag))
    return True


class BusinessRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[BusinessRule]:
        stmt = select(BusinessRule).order_by(
            BusinessRule.scope, BusinessRule.priority, BusinessRule.trigger
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get(self, rule_id: uuid.UUID) -> BusinessRule | None:
        stmt = select(BusinessRule).where(BusinessRule.id == rule_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_for_business(
        self,
        business_id: uuid.UUID,
        plan: str | None = None,
        entitlements: dict[str, Any] | None = None,
    ) -> list[BusinessRule]:
        """Return the resolved, de-duplicated, ordered rule list for one business.

        Resolution order (lower scope wins for the same trigger):
          business > plan > global

        Feature conditions are evaluated against entitlements when provided;
        rules whose condition is not met are excluded.
        """
        stmt = (
            select(BusinessRule)
            .where(BusinessRule.is_active.is_(True))
            .where(
                or_(
                    BusinessRule.scope == "global",
                    *(
                        [BusinessRule.scope == "plan",
                         BusinessRule.plan == plan]
                        if plan else []
                    ),
                    BusinessRule.business_id == business_id,
                )
            )
            .order_by(BusinessRule.priority)
        )
        # Use a broader OR that covers the three cases correctly
        stmt = (
            select(BusinessRule)
            .where(BusinessRule.is_active.is_(True))
            .where(
                or_(
                    BusinessRule.scope == "global",
                    BusinessRule.business_id == business_id,
                )
                if not plan else
                or_(
                    BusinessRule.scope == "global",
                    (BusinessRule.scope == "plan") & (BusinessRule.plan == plan),
                    BusinessRule.business_id == business_id,
                )
            )
            .order_by(BusinessRule.priority)
        )

        all_rules: list[BusinessRule] = list(
            (await self.session.execute(stmt)).scalars().all()
        )

        # Filter by feature_condition
        ents = entitlements or {}
        active = [r for r in all_rules if _eval_condition(r.feature_condition, ents)]

        # De-duplicate by trigger: business > plan > global
        _scope_rank = {"business": 0, "plan": 1, "global": 2}
        seen: dict[str, BusinessRule] = {}
        for rule in sorted(active, key=lambda r: _scope_rank.get(r.scope, 99)):
            if rule.trigger not in seen:
                seen[rule.trigger] = rule

        # Return sorted by priority (ascending)
        return sorted(seen.values(), key=lambda r: r.priority)

    async def create(self, data: dict) -> BusinessRule:
        rule = BusinessRule(**data)
        self.session.add(rule)
        await self.session.flush()
        return rule

    async def update(self, rule: BusinessRule, data: dict) -> BusinessRule:
        for key, value in data.items():
            setattr(rule, key, value)
        await self.session.flush()
        return rule

    async def delete(self, rule: BusinessRule) -> None:
        await self.session.delete(rule)
        await self.session.flush()
