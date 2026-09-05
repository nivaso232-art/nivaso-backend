"""Super-admin AI chat endpoint.

POST /super-admin/chat

Accepts a message and the conversation history from the client, runs the
super-admin agent tool loop, and returns the reply.  No conversation rows
are persisted — history is client-managed and re-sent on every request.

Auth: same as all other /super-admin routes — requires X-Super-Admin-Key
header or a Bearer JWT with role="super_admin".  The dependency is applied
at router-registration time in main.py (not repeated here).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import SuperAdminContext
from app.api.deps import get_session

router = APIRouter(prefix="/chat", tags=["super-admin:chat"])


class HistoryMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: Any  # str for simple turns; list for tool-use turns (replayed by client)


class SuperAdminChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None


class SuperAdminChatResponse(BaseModel):
    reply: str
    tools_used: list[str]


@router.post("", response_model=SuperAdminChatResponse)
async def super_admin_chat(
    body: SuperAdminChatRequest,
    session: AsyncSession = Depends(get_session),
) -> SuperAdminChatResponse:
    """Run one super-admin agent turn and return the reply.

    The client owns the conversation history and must re-send it on each
    request.  Simple history format:
        [{"role": "user", "content": "list businesses"},
         {"role": "assistant", "content": "Here are the businesses..."}]
    """
    from app.agent.super_admin_runner import SuperAdminAgentRunner

    ctx = SuperAdminContext(session=session)
    runner = SuperAdminAgentRunner(ctx=ctx, model=body.model)

    reply, tools_used = await runner.run(
        history=body.history,
        user_text=body.message,
    )

    return SuperAdminChatResponse(reply=reply, tools_used=tools_used)
