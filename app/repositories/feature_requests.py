"""Repository for feature_requests."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feature_request import FeatureRequest


class FeatureRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        business_id: uuid.UUID,
        feature: str,
        reason: str | None,
    ) -> FeatureRequest:
        req = FeatureRequest(
            business_id=business_id,
            feature=feature,
            reason=reason,
            status="pending",
        )
        self.session.add(req)
        await self.session.flush()
        return req

    async def get(self, request_id: uuid.UUID) -> FeatureRequest | None:
        stmt = select(FeatureRequest).where(FeatureRequest.id == request_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_business(
        self, business_id: uuid.UUID
    ) -> Sequence[FeatureRequest]:
        stmt = (
            select(FeatureRequest)
            .where(FeatureRequest.business_id == business_id)
            .order_by(FeatureRequest.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_pending(self) -> Sequence[FeatureRequest]:
        stmt = (
            select(FeatureRequest)
            .where(FeatureRequest.status == "pending")
            .order_by(FeatureRequest.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_all(
        self, *, status: str | None = None
    ) -> Sequence[FeatureRequest]:
        stmt = select(FeatureRequest).order_by(FeatureRequest.created_at.desc())
        if status:
            stmt = stmt.where(FeatureRequest.status == status)
        return (await self.session.execute(stmt)).scalars().all()

    async def review(
        self,
        request_id: uuid.UUID,
        *,
        status: str,
        reviewed_by: str,
        notes: str | None,
    ) -> FeatureRequest | None:
        req = await self.get(request_id)
        if req is None:
            return None
        req.status = status
        req.reviewed_by = reviewed_by
        req.reviewed_at = datetime.now(timezone.utc)
        req.notes = notes
        await self.session.flush()
        return req
