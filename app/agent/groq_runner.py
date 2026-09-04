"""Groq agent turn runner.

Drop-in alternative to AgentRunner/GeminiAgentRunner that uses Groq's
OpenAI-compatible API.  Groq provides fast inference for open models
(Llama, DeepSeek, …) and also hosts selected proprietary models via its
OpenAI-compatible endpoint.

Supported models (configured in models_registry.py):
  openai/gpt-oss-120b             — GPT-OSS 120 B via Groq
  llama-3.3-70b-versatile         — Meta Llama 3.3 70 B, tool-call capable
  llama3-groq-70b-8192-tool-use-preview — tool-use-optimised Llama 3
  deepseek-r1-distill-llama-70b   — DeepSeek reasoning model

The Groq SDK mirrors the OpenAI Python SDK surface so the tool-calling loop
is almost identical to the standard OpenAI pattern.  The main differences
from the Anthropic runner are:

* Messages use the OpenAI ``role``/``content``/``tool_calls`` schema.
* The system prompt is the first message (``role="system"``), not a separate
  parameter.
* Tool results use ``role="tool"`` with a ``tool_call_id`` reference.
* There is no prompt caching; the system/history split is kept for clarity.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import structlog
from groq import AsyncGroq

from app.agent.context import ToolContext
from app.agent.prompts import build_cached_system, build_turn_context
from app.agent.registry import TOOLS_BY_NAME
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

_client = AsyncGroq(api_key=settings.groq_api_key)


def _normalize_schema(node: dict[str, Any]) -> dict[str, Any]:
    """Flatten JSON Schema for OpenAI-compatible APIs.

    Handles two differences from what our ToolSpec schemas emit:
    - ``["string","null"]`` → single type (OpenAI doesn't accept array types).
    - null enum values → dropped, field marked optional by caller instead.
    """
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key == "type" and isinstance(value, list):
            non_null = [t for t in value if t != "null"]
            result["type"] = non_null[0] if non_null else "string"
        elif key == "properties" and isinstance(value, dict):
            result["properties"] = {k: _normalize_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            result["items"] = _normalize_schema(value)
        elif key == "enum" and isinstance(value, list):
            result["enum"] = [v for v in value if v is not None]
        else:
            result[key] = value
    return result


def _spec_to_openai_tool(spec: ToolSpec, *, strict: bool = True) -> dict[str, Any]:
    """Convert a ToolSpec to the OpenAI function-calling format Groq expects.

    Groq's strict mode validates that the model provides every property listed
    in ``required``. Our tool schemas mark nullable parameters as
    ``type: ["X","null"]`` but still include them in ``required`` (Anthropic
    treats them as optional in strict mode; Groq does not).

    To avoid 400 validation errors, nullable properties are removed from
    ``required`` before sending to Groq.  The model can still provide them —
    they just won't be rejected when omitted.
    """
    raw = spec.input_schema
    properties = raw.get("properties", {})

    # Identify properties that are nullable (type is an array containing "null").
    nullable_keys = {
        k for k, v in properties.items()
        if isinstance(v.get("type"), list) and "null" in v["type"]
    }

    # Build a normalised schema with nullable props removed from required.
    normalised = _normalize_schema(raw)
    if nullable_keys and "required" in normalised:
        normalised = {
            **normalised,
            "required": [r for r in normalised["required"] if r not in nullable_keys],
        }

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": normalised,
            "strict": strict,
        },
    }


def _history_to_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Rebuild OpenAI-format messages from the stored append-only message log.

    Same single-pass pairing as the Gemini runner: consecutive TOOL_CALL rows
    become one assistant turn with ``tool_calls``, the TOOL_RESULT rows that
    follow become individual ``role="tool"`` messages.  A dangling call
    (no matching result — server crash) is skipped.
    """
    result: list[dict[str, Any]] = []
    msgs = list(messages)
    i = 0

    while i < len(msgs):
        msg = msgs[i]

        if msg.message_type == MessageType.TOOL_CALL:
            tool_calls: list[dict[str, Any]] = []
            while i < len(msgs) and msgs[i].message_type == MessageType.TOOL_CALL:
                payload = msgs[i].payload or {}
                tool_calls.append({
                    "id": payload.get("tool_use_id", f"call_{i}"),
                    "type": "function",
                    "function": {
                        "name": payload.get("tool", ""),
                        "arguments": json.dumps(payload.get("arguments", {})),
                    },
                })
                i += 1

            result_rows: list[dict[str, Any]] = []
            while i < len(msgs) and msgs[i].message_type == MessageType.TOOL_RESULT:
                payload = msgs[i].payload or {}
                raw = payload.get("result", "")
                result_rows.append({
                    "role": "tool",
                    "tool_call_id": payload.get("tool_use_id", ""),
                    "content": raw if isinstance(raw, str) else json.dumps(raw),
                })
                i += 1

            if not result_rows:
                log.warning("history_dangling_tool_call_skipped_groq", count=len(tool_calls))
                continue

            result.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
            result.extend(result_rows)

        elif msg.sender_type == SenderType.CUSTOMER:
            result.append({"role": "user", "content": msg.content or ""})
            i += 1

        elif msg.sender_type == SenderType.ASSISTANT:
            if msg.content:
                result.append({"role": "assistant", "content": msg.content})
            i += 1

        else:
            i += 1

    return result


class GroqAgentRunner:
    """Drives a single agent turn on Groq's OpenAI-compatible inference API."""

    def __init__(
        self,
        ctx: ToolContext,
        *,
        model: str | None = None,
        extra_tools: Sequence[ToolSpec] = (),
        allowed_tool_names: frozenset[str] | None = None,
        max_iterations_override: int | None = None,
    ) -> None:
        self.ctx = ctx
        self.model = model or settings.groq_model
        self.extra_tools = extra_tools
        self.admin_mode = bool(extra_tools)
        self.allowed_tool_names = allowed_tool_names
        self.max_iterations_override = max_iterations_override

        base_registry = TOOLS_BY_NAME
        if allowed_tool_names is not None:
            base_registry = {k: v for k, v in TOOLS_BY_NAME.items() if k in allowed_tool_names}
        self._tools_by_name = {**base_registry, **{t.name: t for t in extra_tools}}

    async def run(
        self,
        *,
        history: Sequence[Message],
        user_text: str,
        categories: Sequence[str] = (),
        knowledge_titles: Sequence[str] = (),
    ) -> str:
        started_at = time.monotonic()
        conversation_id_str = str(self.ctx.conversation_id)

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

        # System message is the first message for OpenAI-compatible APIs.
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
        messages += _history_to_messages(history)
        messages.append({"role": "user", "content": f"{turn_ctx}\n\n{user_text}"})

        # Build tools: base tools strict, admin extra_tools non-strict.
        all_tools: list[dict[str, Any]] = [
            _spec_to_openai_tool(spec, strict=True)
            for spec in self._tools_by_name.values()
            if spec not in list(self.extra_tools)
        ] + [
            _spec_to_openai_tool(spec, strict=False)
            for spec in self.extra_tools
        ]

        total_input = total_output = 0
        iterations = total_tool_calls = 0
        finish_reason: str | None = None
        last_text = ""

        conv_svc = ConversationService(self.ctx.session, self.ctx.business_id)
        runs_repo = AgentRunRepository(self.ctx.session, self.ctx.business_id)

        try:
            async with UnitOfWork(self.ctx.session):
                while iterations < (self.max_iterations_override or settings.agent_max_iterations):
                    iterations += 1

                    kwargs: dict[str, Any] = {
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": settings.agent_max_tokens,
                    }
                    if all_tools:
                        kwargs["tools"] = all_tools
                        kwargs["tool_choice"] = "auto"

                    response = await _client.chat.completions.create(**kwargs)

                    usage = response.usage
                    if usage:
                        total_input += usage.prompt_tokens or 0
                        total_output += usage.completion_tokens or 0

                    choice = response.choices[0] if response.choices else None
                    if choice is None:
                        log.warning("groq_no_choice")
                        break

                    finish_reason = choice.finish_reason
                    assistant_msg = choice.message

                    if assistant_msg.content:
                        last_text = assistant_msg.content

                    tool_calls = assistant_msg.tool_calls or []
                    if not tool_calls:
                        break

                    total_tool_calls += len(tool_calls)

                    # Append assistant turn with tool_calls to message history.
                    messages.append({
                        "role": "assistant",
                        "content": assistant_msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    })

                    for tc in tool_calls:
                        tool_name = tc.function.name or ""
                        try:
                            arguments = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError:
                            arguments = {}

                        await conv_svc.record_tool_call(
                            conversation=self.ctx.conversation,
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_use_id=tc.id,
                        )

                        is_error = False
                        result: Any
                        spec = self._tools_by_name.get(tool_name)
                        if spec is None:
                            is_error = True
                            result = f"Unknown tool: {tool_name}"
                            log.warning("groq_unknown_tool", tool=tool_name)
                        else:
                            try:
                                result = await spec.execute(self.ctx, arguments)
                            except Exception as exc:
                                is_error = True
                                result = str(exc)
                                log.warning("groq_tool_error", tool=tool_name, error=str(exc))

                        await conv_svc.record_tool_result(
                            conversation=self.ctx.conversation,
                            tool_name=tool_name,
                            result=result,
                            tool_use_id=tc.id,
                            is_error=is_error,
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result if isinstance(result, str) else json.dumps(result, default=str),
                        })

                if last_text:
                    await conv_svc.record_assistant_reply(
                        conversation=self.ctx.conversation,
                        content=last_text,
                        status=MessageStatus.PENDING,
                    )

                latency_ms = int((time.monotonic() - started_at) * 1000)
                run = AgentRun(
                    conversation_id=self.ctx.conversation_id,
                    model=self.model,
                    effort=settings.agent_effort,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    iterations=iterations,
                    tool_calls=total_tool_calls,
                    stop_reason=finish_reason,
                    latency_ms=latency_ms,
                    metadata_={"provider": "groq"},
                )
                await runs_repo.add(run)

                log.info(
                    "agent_turn_complete",
                    provider="groq",
                    conversation_id=conversation_id_str,
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
                provider="groq",
                conversation_id=conversation_id_str,
                error=str(exc),
            )
            raise AgentError(f"Agent turn failed: {exc}") from exc

        return last_text or "I'm sorry, something went wrong. Please try again."
