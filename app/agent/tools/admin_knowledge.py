"""Admin-only tools: knowledge base management via the AI.

These tools are injected ONLY when the request comes through the admin
panel (admin_mode=True in ChatRequest). They are never available on
WhatsApp or Telegram channels, and never in customer-facing web chats.

The isolation is structural: admin_mode requires the X-Internal-Key header
which customers do not have. The model has no way to call these tools unless
the server explicitly adds them to the turn.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, schema, string_prop
from app.models.enums import KnowledgeStatus
from app.models.knowledge import Knowledge
from app.repositories.knowledge import KnowledgeRepository


def _is_real_query(q: str) -> bool:
    """Return False when the model passes a placeholder/artifact instead of a real query."""
    s = q.strip().lower()
    if not s:
        return False
    # Common model artifacts: XML tags, sentinel words, empty-marker strings
    if s.startswith("<") or s.startswith("/"):
        return False
    if s in ("all", "none", "null", "n/a", "*", "-", "list all", "show all"):
        return False
    return True


async def list_knowledge_articles(
    ctx: ToolContext,
    query: str = "",
) -> dict[str, Any]:
    """Pass query='' or 'all' to list all articles, or a real keyword to filter."""
    repo = KnowledgeRepository(ctx.session, ctx.business_id)
    if _is_real_query(query):
        hits = await repo.search(query.strip(), limit=20, published_only=False)
        articles = [h.article for h in hits]
    else:
        articles = list(await repo.list(limit=100, order_by=Knowledge.title))

    return {
        "count": len(articles),
        "articles": [
            {
                "id": str(a.id),
                "title": a.title,
                "status": a.status.value,
                "keywords": a.keywords,
            }
            for a in articles
        ],
        "note": (
            "Use article 'id' values when calling update_knowledge_article."
            if articles
            else "No articles found. You can create one with create_knowledge_article."
        ),
    }


async def create_knowledge_article(
    ctx: ToolContext,
    title: str,
    content: str,
    keywords: list[str],
    status: str,
) -> dict[str, Any]:
    try:
        kb_status = KnowledgeStatus(status)
    except ValueError:
        kb_status = KnowledgeStatus.DRAFT

    article = Knowledge(
        title=title.strip(),
        content=content.strip(),
        keywords=[kw.strip().lower() for kw in keywords if kw.strip()],
        status=kb_status,
        source="admin_chat",
        metadata_={},
    )
    repo = KnowledgeRepository(ctx.session, ctx.business_id)
    await repo.add(article)

    return {
        "id": str(article.id),
        "title": article.title,
        "status": article.status.value,
        "message": f"Article '{article.title}' created as {article.status.value}.",
    }


async def update_knowledge_article(
    ctx: ToolContext,
    article_id: str,
    title: str | None = None,
    content: str | None = None,
    keywords: list[str] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    try:
        aid = uuid.UUID(article_id)
    except ValueError:
        return {"error": f"'{article_id}' is not a valid article ID. Use list_knowledge_articles to find IDs."}

    repo = KnowledgeRepository(ctx.session, ctx.business_id)
    article = await repo.get(aid)
    if article is None:
        return {"error": f"Article {article_id} not found. Use list_knowledge_articles to see available articles."}

    if title is not None:
        article.title = title.strip()
    if content is not None:
        article.content = content.strip()
    if keywords is not None:
        article.keywords = [kw.strip().lower() for kw in keywords if kw.strip()]
    if status is not None:
        try:
            article.status = KnowledgeStatus(status)
        except ValueError:
            pass

    await ctx.session.flush()

    return {
        "id": str(article.id),
        "title": article.title,
        "status": article.status.value,
        "message": f"Article updated successfully.",
    }


# ── Tool specs ────────────────────────────────────────────────────────────────

LIST_KNOWLEDGE_ARTICLES = ToolSpec(
    name="list_knowledge_articles",
    description=(
        "List all knowledge base articles (draft, published, and archived). "
        "Use this to find an article's ID before updating it, or to audit what "
        "content exists. Pass query='' to list all, or a keyword to filter."
    ),
    input_schema=schema(
        properties={
            "query": string_prop(
                "Search keyword to filter articles, or empty string '' to list all."
            ),
        }
    ),
    handler=list_knowledge_articles,
    strict=False,
)

CREATE_KNOWLEDGE_ARTICLE = ToolSpec(
    name="create_knowledge_article",
    description=(
        "Create a new knowledge base article. Draft content with the admin first, "
        "then call this to save it. Use status='draft' unless the admin explicitly "
        "says to publish. Include Tanglish/slang terms in keywords so customers "
        "searching in mixed language can find it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short, descriptive title. e.g. 'How to Redeem Your Steam Key'",
            },
            "content": {
                "type": "string",
                "description": "Full article body. Markdown-style formatting is fine.",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Search keywords including Tanglish/slang. "
                    "e.g. ['redeem', 'steam key', 'activate', 'key kaam nahi']"
                ),
            },
            "status": {
                "type": "string",
                "enum": ["draft", "published"],
                "description": "Use 'draft' by default. Only 'published' if admin explicitly asks.",
            },
        },
        "required": ["title", "content", "keywords", "status"],
        "additionalProperties": False,
    },
    handler=create_knowledge_article,
    strict=False,
)

UPDATE_KNOWLEDGE_ARTICLE = ToolSpec(
    name="update_knowledge_article",
    description=(
        "Update an existing knowledge article by its ID. "
        "Get the ID first with list_knowledge_articles. "
        "Pass only the fields that should change; omit fields to leave them unchanged."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "article_id": {
                "type": "string",
                "description": "UUID from list_knowledge_articles.",
            },
            "title": {
                "type": ["string", "null"],
                "description": "New title, or null to keep the current one.",
            },
            "content": {
                "type": ["string", "null"],
                "description": "New content, or null to keep the current one.",
            },
            "keywords": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": "New keyword list (replaces the whole list), or null to keep current.",
            },
            "status": {
                "type": ["string", "null"],
                "enum": ["draft", "published", "archived", None],
                "description": "New status, or null to keep current.",
            },
        },
        "required": ["article_id", "title", "content", "keywords", "status"],
        "additionalProperties": False,
    },
    handler=update_knowledge_article,
    strict=False,
)

ADMIN_TOOLS: tuple[ToolSpec, ...] = (
    LIST_KNOWLEDGE_ARTICLES,
    CREATE_KNOWLEDGE_ARTICLE,
    UPDATE_KNOWLEDGE_ARTICLE,
)
