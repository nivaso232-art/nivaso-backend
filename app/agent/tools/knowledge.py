"""Knowledge tool: ``search_knowledge``.

This tool's *description* is load-bearing. It is where the Tanglish-to-English
translation step is requested, and that step is what stands in for an embedding
model. The retrieval underneath is Postgres FTS over an English tsvector, so
passing "Bro game launch aagala" through verbatim retrieves nothing useful -
the description has to make the translation obvious enough that the model does
it every time.

If retrieval quality ever looks poor, check two things before reaching for
embeddings: whether the model is actually translating (the ``query`` recorded
in ``messages.payload`` shows this directly), and whether the article has
Tanglish terms in its ``keywords`` array.
"""

from __future__ import annotations

from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, integer_prop, schema, string_prop
from app.models.enums import ConversationState


async def search_knowledge(
    ctx: ToolContext, query: str, limit: int | None = None
) -> dict[str, Any]:
    answers = await ctx.knowledge.search(query, limit=limit or 3)

    # A knowledge search means the customer has a problem, not a purchase
    # question. Reflect that in conversation state - without touching order
    # state (rule 8).
    if ctx.conversation.state is not ConversationState.SUPPORT:
        await ctx.conversations.set_state(ctx.conversation, ConversationState.SUPPORT)

    return {
        "query": query,
        "count": len(answers),
        "articles": [
            {
                "title": answer.title,
                "content": answer.content,
                "truncated": answer.truncated,
            }
            for answer in answers
        ],
        "guidance": (
            "Answer the customer using these articles, in their language and "
            "register. Do not quote the article verbatim - explain it."
            if answers
            else "Nothing matched. Try once more with different English "
            "keywords. If it still finds nothing, do not invent a fix - "
            "call create_support_ticket."
        ),
    }


SEARCH_KNOWLEDGE = ToolSpec(
    name="search_knowledge",
    description=(
        "Search this business's help articles for troubleshooting steps, "
        "policies, and how-to instructions. Use this whenever the customer "
        "reports a problem rather than asking to buy something.\n\n"
        "IMPORTANT: the query must be ENGLISH KEYWORDS describing the "
        "problem. Customers often write in Tamil, Tanglish, or mixed "
        "language - translate first, then search. Examples:\n"
        '  "Bro game launch aagala"  -> "game launcher not starting error"\n'
        '  "download panna mudiyala" -> "cannot download game"\n'
        '  "OTP varala"              -> "OTP not received verification code"\n'
        "Search by symptom, not by product name."
    ),
    input_schema=schema(
        properties={
            "query": string_prop(
                "English keywords describing the problem or topic. "
                "Translate from the customer's language first."
            ),
            "limit": integer_prop(
                "Maximum articles to return. Defaults to 3.",
                minimum=1,
                maximum=5,
                nullable=True,
            ),
        }
    ),
    handler=search_knowledge,
)
