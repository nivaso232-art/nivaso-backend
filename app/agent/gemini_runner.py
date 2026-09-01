"""Gemini agent turn runner.

A drop-in alternative to :class:`app.agent.runner.AgentRunner` that speaks
Google's Gemini API instead of Anthropic's. Same public surface — construct
with a :class:`ToolContext` and call ``run(...)`` — so the webhook/web handlers
don't care which provider is active (see ``app/agent/factory.py``).

The moving parts that differ from the Claude runner:

* **Tools.** Gemini wants ``function_declarations`` whose parameters are an
  OpenAPI-subset ``Schema`` — not JSON-Schema. ``_to_gemini_schema`` converts
  the tool's ``input_schema`` (including the ``["string", "null"]`` nullable
  idiom → ``nullable=True``) and drops keys Gemini rejects (``additionalProperties``).
* **Messages.** Gemini uses ``contents`` with ``user``/``model`` roles and
  ``function_call`` / ``function_response`` parts. ``_history_to_contents``
  rebuilds them from the append-only message log, pairing consecutive
  tool_call rows with the tool_result rows that follow (mirroring the Claude
  runner's reconstruction).
* **No prompt caching.** Gemini's context caching is a separate, explicit API;
  the cached/volatile split still helps token count but there is no
  ``cache_control`` breakpoint to set here.

Everything else — the tool loop, the append-only audit rows (rule 5), the
``AgentRun`` metrics row — matches the Claude runner one-to-one.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import structlog
from google import genai
from google.genai import types

from app.agent.context import ToolContext
from app.agent.prompts import build_cached_system, build_turn_context
from app.agent.registry import TOOLS, TOOLS_BY_NAME
from app.agent.tools.base import ToolSpec
from app.core.config import settings
from app.core.errors import AgentError
from app.core.uow import UnitOfWork
from app.models.agent_run import AgentRun
from app.models.conversation import Message
from app.models.enums import MessageStatus, MessageType, SenderType
from app.repositories.agent_runs import AgentRunRepository
from app.services.conversation_service import ConversationService

log = structlog.get_logger(__name__)

_client = genai.Client(api_key=settings.gemini_api_key)

_JSON_TO_GEMINI_TYPE = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "object": "OBJECT",
    "array": "ARRAY",
}


def _to_gemini_schema(node: dict[str, Any]) -> types.Schema:
    """Convert a strict JSON-Schema node to a Gemini ``types.Schema``.

    Handles the two things Gemini won't take verbatim from our tool schemas:
    the ``["string", "null"]`` nullable idiom (→ single type + ``nullable``),
    and ``additionalProperties`` (dropped — Gemini has no such field).
    """
    raw_type = node.get("type")
    nullable = False
    if isinstance(raw_type, list):
        non_null = [t for t in raw_type if t != "null"]
        nullable = "null" in raw_type
        raw_type = non_null[0] if non_null else "string"

    kwargs: dict[str, Any] = {}
    if raw_type is not None:
        kwargs["type"] = _JSON_TO_GEMINI_TYPE.get(raw_type, "STRING")
    if node.get("description"):
        kwargs["description"] = node["description"]
    if "enum" in node:
        # Anthropic allows None in an enum to mean "optional"; Gemini requires
        # all-string enums, so strip None and mark the field nullable instead.
        values = [v for v in node["enum"] if v is not None]
        if len(values) != len(node["enum"]):
            nullable = True
        kwargs["enum"] = values
    if "minimum" in node:
        kwargs["minimum"] = float(node["minimum"])
    if "maximum" in node:
        kwargs["maximum"] = float(node["maximum"])
    if raw_type == "object":
        properties = node.get("properties", {})
        kwargs["properties"] = {
            key: _to_gemini_schema(value) for key, value in properties.items()
        }
        if node.get("required"):
            kwargs["required"] = list(node["required"])
    if raw_type == "array" and node.get("items"):
        kwargs["items"] = _to_gemini_schema(node["items"])
    if nullable:
        kwargs["nullable"] = True

    return types.Schema(**kwargs)


def _function_declarations() -> list[types.FunctionDeclaration]:
    """The 9 tools, rendered as Gemini function declarations."""
    return [
        types.FunctionDeclaration(
            name=spec.name,
            description=spec.description,
            parameters=_to_gemini_schema(spec.input_schema),
        )
        for spec in TOOLS
    ]


def _as_response_dict(result: Any, is_error: bool) -> dict[str, Any]:
    """Gemini's FunctionResponse.response must be a dict."""
    if is_error:
        return {"error": str(result)}
    if isinstance(result, dict):
        return result
    return {"result": result}


def _history_to_contents(messages: Sequence[Message]) -> list[types.Content]:
    """Rebuild Gemini ``contents`` from the stored message log.

    Same single-pass pairing as the Claude runner: consecutive TOOL_CALL rows
    become one ``model`` turn of ``function_call`` parts, the TOOL_RESULT rows
    that follow become one ``user`` turn of ``function_response`` parts. A
    dangling tool call (server crashed before recording the result) is skipped
    — Gemini rejects a function_call with no matching function_response.
    """
    result: list[types.Content] = []
    msgs = list(messages)
    i = 0

    while i < len(msgs):
        msg = msgs[i]

        if msg.message_type == MessageType.TOOL_CALL:
            call_parts: list[types.Part] = []
            while i < len(msgs) and msgs[i].message_type == MessageType.TOOL_CALL:
                payload = msgs[i].payload or {}
                call_parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=payload.get("tool", ""),
                            args=payload.get("arguments", {}) or {},
                        )
                    )
                )
                i += 1

            response_parts: list[types.Part] = []
            while i < len(msgs) and msgs[i].message_type == MessageType.TOOL_RESULT:
                payload = msgs[i].payload or {}
                response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=payload.get("tool", ""),
                            response=_as_response_dict(
                                payload.get("result"), payload.get("is_error", False)
                            ),
                        )
                    )
                )
                i += 1

            if not response_parts:
                log.warning("history_dangling_tool_call_skipped", count=len(call_parts))
                continue

            result.append(types.Content(role="model", parts=call_parts))
            result.append(types.Content(role="user", parts=response_parts))

        elif msg.sender_type == SenderType.CUSTOMER:
            result.append(
                types.Content(role="user", parts=[types.Part(text=msg.content or "")])
            )
            i += 1

        elif msg.sender_type == SenderType.ASSISTANT:
            if msg.content:
                result.append(
                    types.Content(role="model", parts=[types.Part(text=msg.content)])
                )
            i += 1

        else:
            # SYSTEM_NOTE, AGENT (human) — audit rows, not part of the prompt.
            i += 1

    return result


class GeminiAgentRunner:
    """Drives a single agent turn on Gemini: call the model, execute tools, reply."""

    def __init__(
        self,
        ctx: ToolContext,
        *,
        model: str | None = None,
        extra_tools: Sequence[ToolSpec] = (),
    ) -> None:
        self.ctx = ctx
        self.model = model or settings.gemini_model
        self.extra_tools = extra_tools
        self.admin_mode = bool(extra_tools)
        self._tools_by_name = {
            **TOOLS_BY_NAME,
            **{t.name: t for t in extra_tools},
        }

    async def run(
        self,
        *,
        history: Sequence[Message],
        user_text: str,
        categories: Sequence[str] = (),
        knowledge_titles: Sequence[str] = (),
    ) -> str:
        started_at = time.monotonic()
        # Capture as a primitive up front: after a rollback the ORM objects are
        # expired, so reading self.ctx.conversation_id in the except handler
        # would trigger a lazy reload (and a MissingGreenlet) that masks the
        # real error.
        conversation_id_str = str(self.ctx.conversation_id)

        # build_cached_system returns Anthropic-style text blocks; Gemini takes a
        # plain system_instruction string, so pull the text back out.
        system_blocks = build_cached_system(
            self.ctx.business,
            categories=categories,
            knowledge_titles=knowledge_titles,
        )
        system_text = system_blocks[0]["text"]

        turn_ctx = build_turn_context(
            customer=self.ctx.customer,
            conversation=self.ctx.conversation,
            admin_mode=self.admin_mode,
        )

        contents = _history_to_contents(history)
        # Volatile turn state rides in front of the new user message, so it is
        # always fresh and never part of any cached prefix.
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text=f"{turn_ctx}\n\n{user_text}")],
            )
        )

        config = types.GenerateContentConfig(
            system_instruction=system_text,
            tools=[types.Tool(
                function_declarations=_function_declarations() + [
                    types.FunctionDeclaration(
                        name=t.name,
                        description=t.description,
                        parameters=_to_gemini_schema(t.input_schema),
                    )
                    for t in self.extra_tools
                ]
            )],
            # We run tools ourselves (to log them and inject ToolContext), so keep
            # the SDK from trying to auto-execute anything.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            max_output_tokens=settings.agent_max_tokens,
        )

        total_input = total_output = total_cache_read = 0
        iterations = total_tool_calls = 0
        finish_reason: str | None = None
        last_text = ""

        conv_svc = ConversationService(self.ctx.session, self.ctx.business_id)
        runs_repo = AgentRunRepository(self.ctx.session, self.ctx.business_id)

        try:
            async with UnitOfWork(self.ctx.session):
                while iterations < settings.agent_max_iterations:
                    iterations += 1

                    response = await _client.aio.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=config,
                    )

                    usage = response.usage_metadata
                    if usage is not None:
                        total_input += usage.prompt_token_count or 0
                        total_output += usage.candidates_token_count or 0
                        total_cache_read += getattr(
                            usage, "cached_content_token_count", 0
                        ) or 0

                    candidate = (
                        response.candidates[0] if response.candidates else None
                    )
                    if candidate is None or candidate.content is None:
                        # Safety block or empty candidate — stop with a fallback.
                        log.warning("gemini_no_candidate")
                        break

                    finish_reason = str(getattr(candidate, "finish_reason", "") or "")
                    parts = candidate.content.parts or []

                    text_chunks = [p.text for p in parts if getattr(p, "text", None)]
                    if text_chunks:
                        last_text = "".join(text_chunks)

                    function_calls = [
                        p.function_call
                        for p in parts
                        if getattr(p, "function_call", None)
                    ]
                    if not function_calls:
                        break

                    total_tool_calls += len(function_calls)

                    # Replay exactly what the model produced on the next turn.
                    contents.append(candidate.content)

                    response_parts: list[types.Part] = []
                    for idx, call in enumerate(function_calls):
                        tool_name = call.name or ""
                        arguments = dict(call.args or {})
                        # Gemini function calls have no stable id; synthesise one
                        # so the tool_call/tool_result rows pair up in the log.
                        tool_use_id = f"gemini-{iterations}-{idx}"

                        await conv_svc.record_tool_call(
                            conversation=self.ctx.conversation,
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_use_id=tool_use_id,
                        )

                        is_error = False
                        result: Any
                        spec = self._tools_by_name.get(tool_name)
                        if spec is None:
                            is_error = True
                            result = f"Unknown tool: {tool_name}"
                            log.warning("unknown_tool", tool=tool_name)
                        else:
                            try:
                                result = await spec.execute(self.ctx, arguments)
                            except Exception as exc:
                                is_error = True
                                result = str(exc)
                                log.warning("tool_error", tool=tool_name, error=str(exc))

                        await conv_svc.record_tool_result(
                            conversation=self.ctx.conversation,
                            tool_name=tool_name,
                            result=result,
                            tool_use_id=tool_use_id,
                            is_error=is_error,
                        )

                        response_parts.append(
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=tool_name,
                                    response=_as_response_dict(result, is_error),
                                )
                            )
                        )

                    contents.append(types.Content(role="user", parts=response_parts))

                if last_text:
                    await conv_svc.record_assistant_reply(
                        conversation=self.ctx.conversation,
                        content=last_text,
                        status=MessageStatus.PENDING,
                    )

                latency_ms = int((time.monotonic() - started_at) * 1000)
                run = AgentRun(
                    conversation_id=self.ctx.conversation_id,
                    model=settings.gemini_model,
                    effort=settings.agent_effort,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cache_read_tokens=total_cache_read,
                    cache_creation_tokens=0,
                    iterations=iterations,
                    tool_calls=total_tool_calls,
                    stop_reason=finish_reason,
                    latency_ms=latency_ms,
                    metadata_={"provider": "gemini"},
                )
                await runs_repo.add(run)

                log.info(
                    "agent_turn_complete",
                    provider="gemini",
                    conversation_id=str(self.ctx.conversation_id),
                    iterations=iterations,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    finish_reason=finish_reason,
                    latency_ms=latency_ms,
                )

        except AgentError:
            raise
        except Exception as exc:
            log.exception(
                "agent_turn_failed",
                provider="gemini",
                conversation_id=conversation_id_str,
                error=str(exc),
            )
            raise AgentError(f"Agent turn failed: {exc}") from exc

        return last_text or "I'm sorry, something went wrong. Please try again."
