"""Custom-field value validation.

The contract other agents (building Service/Offer/Coupon in a later phase)
import to validate the dynamic attributes a business admin has defined for
their tenant (see ``app/models/field_definition.py``) against the raw values
submitted for one entity instance (e.g. a product's ``metadata_`` dict).

Kept provider-agnostic and DB-agnostic: it operates purely on the
``FieldDefinition`` rows handed to it and a plain ``dict`` of values, so it can
be called from any entity's create/update path without that path needing to
know anything about how definitions are stored or fetched.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import Any

from app.core.errors import ValidationError
from app.models.field_definition import FieldDefinition, FieldType


def _option_values(definition: FieldDefinition) -> list[Any]:
    """Flatten ``definition.options`` (``[{"value": ..., "label": ...}]``) to
    the list of acceptable values. Falls back to treating bare scalars as
    values, in case a definition was seeded without the label wrapper."""
    options = definition.options or []
    values: list[Any] = []
    for option in options:
        if isinstance(option, dict):
            values.append(option.get("value"))
        else:
            values.append(option)
    return values


def _coerce_number(key: str, value: Any) -> int | float:
    if isinstance(value, bool):
        raise ValidationError(
            f"Field '{key}' must be a number.", details={"key": key, "value": value}
        )
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            if "." in value or "e" in value.lower():
                return float(value)
            return int(value)
        except ValueError:
            pass
    raise ValidationError(
        f"Field '{key}' must be a number.", details={"key": key, "value": value}
    )


def _coerce_boolean(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    raise ValidationError(
        f"Field '{key}' must be a boolean.", details={"key": key, "value": value}
    )


def _coerce_date(key: str, value: Any) -> str:
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            datetime.date.fromisoformat(value)
            return value
        except ValueError:
            pass
    raise ValidationError(
        f"Field '{key}' must be an ISO date string (YYYY-MM-DD).",
        details={"key": key, "value": value},
    )


def _coerce_text(key: str, value: Any) -> str:
    if isinstance(value, str):
        return value
    raise ValidationError(
        f"Field '{key}' must be a string.", details={"key": key, "value": value}
    )


def _coerce_select(key: str, value: Any, definition: FieldDefinition) -> Any:
    allowed = _option_values(definition)
    if value not in allowed:
        raise ValidationError(
            f"Field '{key}' must be one of {allowed}.",
            details={"key": key, "value": value, "allowed": allowed},
        )
    return value


def _coerce_multiselect(key: str, value: Any, definition: FieldDefinition) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(
            f"Field '{key}' must be a list.", details={"key": key, "value": value}
        )
    allowed = _option_values(definition)
    for item in value:
        if item not in allowed:
            raise ValidationError(
                f"Field '{key}' contains an invalid option: {item!r}. Must be one of {allowed}.",
                details={"key": key, "value": item, "allowed": allowed},
            )
    return value


async def validate_custom_fields(
    definitions: Sequence[FieldDefinition],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Validate ``values`` against ``definitions``; return the cleaned dict.

    Rules:
    - Unknown keys in ``values`` not present in ``definitions`` -> raise ValidationError.
    - A definition with ``required=True`` missing from ``values`` (or ``None``) ->
      raise ValidationError.
    - Type-check/coerce per ``field_type``: NUMBER -> int|float, BOOLEAN -> bool,
      DATE -> ISO date string, SELECT -> must be one of ``definition.options``
      values, MULTISELECT -> list, each must be one of ``options`` values,
      TEXT -> str.
    - Fields not required and absent from ``values`` are simply omitted from
      the returned dict (no null-filling).
    """
    by_key = {definition.key: definition for definition in definitions}

    unknown = [key for key in values if key not in by_key]
    if unknown:
        raise ValidationError(
            "Unknown custom field key(s): " + ", ".join(sorted(unknown)),
            details={"unknown_keys": sorted(unknown)},
        )

    cleaned: dict[str, Any] = {}
    for definition in definitions:
        key = definition.key
        has_value = key in values and values[key] is not None

        if not has_value:
            if definition.required:
                raise ValidationError(
                    f"Field '{key}' is required.", details={"key": key}
                )
            continue

        raw = values[key]
        if definition.field_type == FieldType.NUMBER:
            cleaned[key] = _coerce_number(key, raw)
        elif definition.field_type == FieldType.BOOLEAN:
            cleaned[key] = _coerce_boolean(key, raw)
        elif definition.field_type == FieldType.DATE:
            cleaned[key] = _coerce_date(key, raw)
        elif definition.field_type == FieldType.SELECT:
            cleaned[key] = _coerce_select(key, raw, definition)
        elif definition.field_type == FieldType.MULTISELECT:
            cleaned[key] = _coerce_multiselect(key, raw, definition)
        elif definition.field_type == FieldType.TEXT:
            cleaned[key] = _coerce_text(key, raw)
        else:  # pragma: no cover - exhaustive over FieldType
            raise ValidationError(
                f"Unsupported field type for '{key}': {definition.field_type}",
                details={"key": key, "field_type": str(definition.field_type)},
            )

    return cleaned
