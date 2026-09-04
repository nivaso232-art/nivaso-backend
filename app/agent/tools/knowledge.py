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


async def get_full_article(
    ctx: ToolContext, article_id: str
) -> dict[str, Any]:
    """Fetch the complete, untruncated content of a knowledge article by its ID."""
    import uuid as _uuid

    try:
        aid = _uuid.UUID(article_id)
    except ValueError:
        return {"error": f"'{article_id}' is not a valid article ID. Use the id returned by search_knowledge."}

    try:
        article = await ctx.knowledge.get_or_raise(aid)
    except Exception:
        return {"error": f"Article {article_id} not found."}

    return {
        "id": str(article.id),
        "title": article.title,
        "content": article.content,
        "keywords": article.keywords,
        "status": article.status.value,
    }


GET_FULL_ARTICLE = ToolSpec(
    name="get_full_article",
    description=(
        "Fetch the complete, untruncated content of a knowledge base article by its ID. "
        "Use this after search_knowledge returns truncated=true and the customer still "
        "needs more detail. The article_id comes from a search_knowledge result."
    ),
    input_schema=schema(
        properties={
            "article_id": string_prop(
                "The article UUID returned by search_knowledge."
            ),
        }
    ),
    handler=get_full_article,
)


SEARCH_KNOWLEDGE = ToolSpec(
    name="search_knowledge",
    description=(
        "Search help articles for troubleshooting, policies, and how-to guides. "
        "Use for customer problems, not product questions. "
        "Translate the customer's message to English symptom keywords before searching."
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
