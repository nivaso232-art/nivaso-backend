"""Tool specification.

Every tool is a name, a description, a strict JSON schema, and an async
handler taking ``(ToolContext, **kwargs)``.

Two conventions that matter:

**Strict schemas.** Every tool sets ``strict: True`` with
``additionalProperties: false`` and a complete ``required`` list, so the API
guarantees ``tool_use.input`` validates against the schema. Without it, a
malformed ``quantity: "two"`` reaches the handler and becomes a runtime
TypeError mid-conversation.

**No tenant parameters.** ``business_id``, ``customer_id`` and
``conversation_id`` never appear in a schema - they come from
:class:`~app.agent.context.ToolContext` (rule 4). If you are adding a tool and
reaching for one of those as a parameter, that is the bug.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.agent.context import ToolContext

ToolHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def to_api_tool(self) -> dict[str, Any]:
        """Render for the Anthropic ``tools`` parameter.

        Key order is fixed so the serialised tool list is byte-identical
        between requests - the tool block is part of the cached prefix, and a
        reordered dict silently invalidates the whole cache.
        """
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": self.input_schema,
        }

    async def execute(self, ctx: ToolContext, arguments: dict[str, Any]) -> Any:
        return await self.handler(ctx, **arguments)


def schema(
    *,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Build a strict-mode object schema.

    ``required`` defaults to *every* property. Strict mode requires that
    optional parameters still appear in ``required`` with a nullable type, so
    the honest way to express "optional" here is ``["string", "null"]`` in the
    property type - see ``get_order_status``.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }


def string_prop(description: str, *, nullable: bool = False) -> dict[str, Any]:
    return {
        "type": ["string", "null"] if nullable else "string",
        "description": description,
    }


def integer_prop(
    description: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    nullable: bool = False,
) -> dict[str, Any]:
    prop: dict[str, Any] = {
        "type": ["integer", "null"] if nullable else "integer",
        "description": description,
    }
    if minimum is not None:
        prop["minimum"] = minimum
    if maximum is not None:
        prop["maximum"] = maximum
    return prop


def enum_prop(description: str, values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values, "description": description}
