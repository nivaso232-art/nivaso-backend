"""Field-definition reads/writes — the admin-defined custom fields schema."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.field_definition import FieldDefinition, FieldEntityType
from app.repositories.base import BaseRepository


class FieldDefinitionRepository(BaseRepository[FieldDefinition]):
    model = FieldDefinition

    async def list_by_entity_type(
        self, entity_type: FieldEntityType
    ) -> Sequence[FieldDefinition]:
        stmt = (
            self._scoped()
            .where(FieldDefinition.entity_type == entity_type)
            .order_by(FieldDefinition.sort_order, FieldDefinition.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_key(
        self, entity_type: FieldEntityType, key: str
    ) -> FieldDefinition | None:
        stmt = self._scoped().where(
            FieldDefinition.entity_type == entity_type, FieldDefinition.key == key
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
