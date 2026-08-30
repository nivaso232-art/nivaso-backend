"""Knowledge retrieval for support answers - no embeddings.

The division of labour that replaces a vector store:

* The **model** turns "Bro game launch aagala" into English keywords. It is
  already reading the message; asking it to also normalise the language costs
  nothing extra and handles Tanglish, code-switching, and slang better than
  English FTS on raw input would.
* **Postgres** does keyword retrieval, which it is genuinely good at, with a
  trigram fallback for typos.

Two consequences worth knowing:

1. Recall depends on the prompt instruction holding. ``search_raw`` exists as
   the safety net for when it does not.
2. The ``keywords`` column is the tuning knob. Add the Tanglish terms
   customers actually use and even raw-input search starts hitting.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.knowledge import Knowledge
from app.repositories.knowledge import KnowledgeHit, KnowledgeRepository

log = structlog.get_logger(__name__)

DEFAULT_LIMIT = 3
MAX_LIMIT = 5
# Trimmed before going into the prompt. A 4000-word troubleshooting doc costs
# real input tokens and buries the relevant paragraph; the model gets enough to
# answer, and the agent console shows the full article to humans.
MAX_CONTENT_CHARS = 1500


@dataclass(frozen=True)
class KnowledgeAnswer:
    """A hit, shaped for the model rather than for a UI."""

    id: uuid.UUID
    title: str
    content: str
    matched_by: str
    truncated: bool


class KnowledgeService:
    def __init__(self, session: AsyncSession, business_id: uuid.UUID) -> None:
        self.session = session
        self.business_id = business_id
        self.knowledge = KnowledgeRepository(session, business_id)

    async def search(
        self, query: str, *, limit: int = DEFAULT_LIMIT
    ) -> list[KnowledgeAnswer]:
        """Search with English keywords supplied by the agent."""
        limit = max(1, min(limit, MAX_LIMIT))
        hits = await self.knowledge.search(query, limit=limit)

        log.info(
            "knowledge_search",
            query=query,
            hits=len(hits),
            matched_by=hits[0].matched_by if hits else None,
        )
        return [self._to_answer(hit) for hit in hits]

    async def search_raw(
        self, raw_message: str, *, limit: int = DEFAULT_LIMIT
    ) -> list[KnowledgeAnswer]:
        """Search using the customer's untranslated message.

        The fallback for when the model skips the translation step. Recall is
        worse on Tanglish input, but a curated ``keywords`` array can carry it -
        which is exactly why that column exists.
        """
        return await self.search(raw_message, limit=limit)

    async def get_or_raise(self, article_id: uuid.UUID) -> Knowledge:
        article = await self.knowledge.get(article_id)
        if article is None:
            raise NotFoundError(
                "Knowledge article not found.", details={"id": str(article_id)}
            )
        return article

    async def index_summary(self, *, limit: int = 50) -> list[dict[str, str]]:
        """Titles only, for the cached system prompt.

        Cheap orientation: the agent learns what topics exist without a tool
        call, so it knows whether searching is worth doing at all.
        """
        articles = await self.knowledge.list_published(limit=limit)
        return [
            {"id": str(article.id), "title": article.title} for article in articles
        ]

    @staticmethod
    def _to_answer(hit: KnowledgeHit) -> KnowledgeAnswer:
        content = hit.article.content
        truncated = len(content) > MAX_CONTENT_CHARS
        if truncated:
            content = content[:MAX_CONTENT_CHARS].rstrip() + "..."
        return KnowledgeAnswer(
            id=hit.article.id,
            title=hit.article.title,
            content=content,
            matched_by=hit.matched_by,
            truncated=truncated,
        )
