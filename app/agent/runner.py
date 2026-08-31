"""Agent turn runner — the conversation loop.

Flow per turn:
  1. Rebuild the Anthropic messages array from the DB history.
  2. Append the new customer message and a volatile turn-context block.
  3. Call the Anthropic Messages API (cached system + tools).
  4. If the response contains tool_use blocks:
       a. Persist tool_call rows (rule 5 audit trail).
       b. Execute each tool handler against ToolContext.
       c. Persist tool_result rows (rule 5).
       d. Append the full assistant turn + tool results and loop back to (3).
  5. On ``end_turn`` (or ``max_tokens``), persist the final assistant message.
  6. Write an ``AgentRun`` row with accumulated token counts for observability.

Max-iterations guard: if the loop reaches ``AGENT_MAX_ITERATIONS`` without an
``end_turn``, the last seen text block is returned. The model almost always
says something coherent at that point, but ``stop_reason = "tool_use"`` in the
``agent_runs`` row is the signal to investigate.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import anthropic
import structlog

from app.agent.context import ToolContext
from app.agent.prompts import build_cached_system, build_turn_context
from app.agent.registry import TOOLS_BY_NAME, api_tools
from app.core.config import settings
from app.core.errors import AgentError
from app.core.uow import UnitOfWork
from app.models.agent_run import AgentRun
from app.models.conversation import Message
from app.models.enums import MessageStatus, MessageType, SenderType
from app.repositories.agent_runs import AgentRunRepository
from app.services.conversation_service import ConversationService

log = structlog.get_logger(__name__)

_client = anthropic.AsyncAnthropic(
    api_key=settings.anthropic_api_key,
    # A Personal / identity-linked key must name the workspace each request acts
    # in; a normal Workspace key does not (leave ANTHROPIC_WORKSPACE_ID blank).
    default_headers=(
        {"anthropic-workspace-id": settings.anthropic_workspace_id}
        if settings.anthropic_workspace_id
        else None
    ),
)

_EFFORT_BUDGET: dict[str, int] = {
    "high": 2_000,
    "xhigh": 8_000,
    "max": 16_000,
}

# Extended thinking requires betas and careful replay of thinking blocks in
# every subsequent turn. Disabled for now; set to True once the channel
# handlers and history reconstruction are verified to handle it correctly.
_THINKING_ENABLED = False


def _to_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _history_to_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert stored message rows to Anthropic Messages API format.

    Tool traffic in the DB is stored as separate rows but the API requires
    them grouped into assistant + user turns:

        assistant:  [{type: "tool_use", id, name, input}, ...]
        user:       [{type: "tool_result", tool_use_id, content, is_error}, ...]

    The algorithm walks the list in a single pass, collecting consecutive
    TOOL_CALL rows into an assistant block, then the following TOOL_RESULT
    rows into a user block.
    """
    result: list[dict[str, Any]] = []
    msgs = list(messages)
    i = 0

    while i < len(msgs):
        msg = msgs[i]

        if msg.message_type == MessageType.TOOL_CALL:
            # Collect all consecutive tool calls as one assistant turn.
            tool_use_blocks: list[dict[str, Any]] = []
            while i < len(msgs) and msgs[i].message_type == MessageType.TOOL_CALL:
                m = msgs[i]
                tool_use_blocks.append({
                    "type": "tool_use",
                    "id": m.tool_use_id or str(m.id),
                    "name": m.payload.get("tool", ""),
                    "input": m.payload.get("arguments", {}),
                })
                i += 1

            # Collect the following tool results as one user turn.
            tool_result_blocks: list[dict[str, Any]] = []
            while i < len(msgs) and msgs[i].message_type == MessageType.TOOL_RESULT:
                m = msgs[i]
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": m.tool_use_id or str(m.id),
                    "content": _to_json(m.payload.get("result", "")),
                    "is_error": m.payload.get("is_error", False),
                })
                i += 1

            # Edge case: if the server crashed between recording tool_call and
            # tool_result rows, the tool_use blocks have no matching results.
            # The Anthropic API rejects an assistant turn with tool_use blocks
            # that has no following user:[tool_result] turn. Skip this incomplete
            # turn entirely — the current user message will re-trigger the tools.
            if not tool_result_blocks:
                log.warning(
                    "history_dangling_tool_call_skipped",
                    count=len(tool_use_blocks),
                )
                continue

            result.append({"role": "assistant", "content": tool_use_blocks})
            result.append({"role": "user", "content": tool_result_blocks})

        elif msg.sender_type == SenderType.CUSTOMER:
            result.append({"role": "user", "content": msg.content or ""})
            i += 1

        elif msg.sender_type == SenderType.ASSISTANT:
            if msg.content:
                result.append({"role": "assistant", "content": msg.content})
            i += 1

        else:
            # SYSTEM_NOTE, AGENT (human agent) — audit rows, not part of the prompt.
            i += 1

    # Defensive: the Anthropic API requires the messages array to end with a
    # user turn. If the last entry is an assistant turn (e.g. the previous
    # reply was recorded but the new inbound has not been appended yet), strip
    # it — the runner will append the current user_text immediately after.
    while result and result[-1]["role"] == "assistant":
        result.pop()

    return result


class AgentRunner:
    """Drives a single agent turn: call the LLM, execute tools, return the reply."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    async def run(
        self,
        *,
        history: Sequence[Message],
        user_text: str,
        categories: Sequence[str] = (),
        knowledge_titles: Sequence[str] = (),
    ) -> str:
        """Execute a full agent turn and return the text reply.

        The caller must:
        - Persist the customer message *before* calling this.
        - Send the returned reply to the customer *after* calling this.

        Both the tool calls/results and the final reply are written to the DB
        inside a single transaction owned by a ``UnitOfWork`` here.
        """
        started_at = time.monotonic()

        system = build_cached_system(
            self.ctx.business,
            categories=categories,
            knowledge_titles=knowledge_titles,
        )

        turn_ctx = build_turn_context(
            customer=self.ctx.customer,
            conversation=self.ctx.conversation,
        )

        # Volatile turn context is injected as the first user/assistant exchange
        # so it lands *after* the cached prefix and does not bust the cache.
        api_messages = _history_to_messages(history)
        if not api_messages:
            api_messages = [
                {"role": "user", "content": turn_ctx},
                {"role": "assistant", "content": "Understood."},
            ]
        else:
            # Prepend to the current message so the agent always has fresh state.
            api_messages.insert(0, {"role": "user", "content": turn_ctx})
            api_messages.insert(1, {"role": "assistant", "content": "Understood."})

        api_messages.append({"role": "user", "content": user_text})

        # Accumulated token counts across all iterations of the tool loop.
        total_input = total_output = total_cache_read = total_cache_creation = 0
        iterations = total_tool_calls = 0
        stop_reason: str | None = None
        last_text = ""
        request_ids: list[str] = []

        # Extended thinking is disabled: thinking blocks must be replayed
        # verbatim in every subsequent assistant turn or the API rejects the
        # request. That requires threading them through _history_to_messages
        # and the assistant_content builder — deferred until fully validated.
        thinking_param: Any = anthropic.NOT_GIVEN

        conv_svc = ConversationService(self.ctx.session, self.ctx.business_id)
        runs_repo = AgentRunRepository(self.ctx.session, self.ctx.business_id)

        try:
            async with UnitOfWork(self.ctx.session):
                while iterations < settings.agent_max_iterations:
                    iterations += 1

                    response = await _client.messages.create(
                        model=settings.agent_model,
                        max_tokens=settings.agent_max_tokens,
                        system=system,
                        tools=api_tools(),
                        messages=api_messages,
                        thinking=thinking_param,
                    )

                    usage = response.usage
                    total_input += usage.input_tokens
                    total_output += usage.output_tokens
                    total_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
                    total_cache_creation += (
                        getattr(usage, "cache_creation_input_tokens", 0) or 0
                    )

                    rid = getattr(response, "_request_id", None)
                    if rid:
                        request_ids.append(str(rid))

                    stop_reason = response.stop_reason

                    text_blocks = [b for b in response.content if b.type == "text"]
                    tool_blocks = [b for b in response.content if b.type == "tool_use"]

                    if text_blocks:
                        last_text = text_blocks[-1].text

                    if not tool_blocks:
                        # end_turn or max_tokens — loop is done.
                        break

                    total_tool_calls += len(tool_blocks)

                    # Append the full assistant turn (text + tool_use) so replay
                    # stays valid even when we restart after a crash mid-turn.
                    assistant_content: list[dict[str, Any]] = []
                    for block in response.content:
                        if block.type == "text":
                            assistant_content.append({"type": "text", "text": block.text})
                        elif block.type == "tool_use":
                            assistant_content.append({
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": dict(block.input),
                            })
                    api_messages.append({"role": "assistant", "content": assistant_content})

                    tool_result_api: list[dict[str, Any]] = []

                    for tool_block in tool_blocks:
                        tool_name = tool_block.name
                        tool_use_id = tool_block.id
                        arguments = dict(tool_block.input)

                        # Rule 5 — record the call before executing.
                        await conv_svc.record_tool_call(
                            conversation=self.ctx.conversation,
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_use_id=tool_use_id,
                        )

                        is_error = False
                        result: Any = None
                        spec = TOOLS_BY_NAME.get(tool_name)
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
                                log.warning(
                                    "tool_error",
                                    tool=tool_name,
                                    error=str(exc),
                                )

                        # Rule 5 — record the result.
                        await conv_svc.record_tool_result(
                            conversation=self.ctx.conversation,
                            tool_name=tool_name,
                            result=result,
                            tool_use_id=tool_use_id,
                            is_error=is_error,
                        )

                        tool_result_api.append({
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": _to_json(result),
                            "is_error": is_error,
                        })

                    api_messages.append({"role": "user", "content": tool_result_api})

                # Persist the final assistant reply.
                if last_text:
                    await conv_svc.record_assistant_reply(
                        conversation=self.ctx.conversation,
                        content=last_text,
                        status=MessageStatus.PENDING,
                    )

                latency_ms = int((time.monotonic() - started_at) * 1000)
                run = AgentRun(
                    conversation_id=self.ctx.conversation_id,
                    model=settings.agent_model,
                    effort=settings.agent_effort,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cache_read_tokens=total_cache_read,
                    cache_creation_tokens=total_cache_creation,
                    iterations=iterations,
                    tool_calls=total_tool_calls,
                    stop_reason=stop_reason,
                    latency_ms=latency_ms,
                    metadata_={"request_ids": request_ids},
                )
                await runs_repo.add(run)

                log.info(
                    "agent_turn_complete",
                    conversation_id=str(self.ctx.conversation_id),
                    iterations=iterations,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cache_read_tokens=total_cache_read,
                    stop_reason=stop_reason,
                    latency_ms=latency_ms,
                )

        except AgentError:
            raise
        except Exception as exc:
            log.exception(
                "agent_turn_failed",
                conversation_id=str(self.ctx.conversation_id),
            )
            raise AgentError(f"Agent turn failed: {exc}") from exc

        return last_text or "I'm sorry, something went wrong. Please try again."
