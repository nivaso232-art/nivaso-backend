"""Knowledge base search - Postgres FTS, no embeddings.

The Tanglish problem is solved one layer up: the agent translates
"Bro game launch aagala" into English keywords before calling
``search_knowledge``. This repository only has to be good at English keyword
retrieval, which Postgres already is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import REAL, cast, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG

from app.models.enums import KnowledgeStatus
from app.models.knowledge import Knowledge
from app.repositories.base import BaseRepository

TRGM_THRESHOLD = 0.2

# Weights for ts_rank, matched to the setweight labels in the generated
# column: {D, C, B, A}. Title (A) and curated keywords (B) dominate; a passing
# mention in a long body (C) should not outrank a purpose-written article.
TS_RANK_WEIGHTS = "{0.1, 0.3, 0.7, 1.0}"


@dataclass(frozen=True)
class KnowledgeHit:
    article: Knowledge
    rank: float
    matched_by: str  # "fts" | "trigram"


class KnowledgeRepository(BaseRepository[Knowledge]):
    model = Knowledge

    async def search(
        self, query: str, *, limit: int = 3, published_only: bool = True
    ) -> list[KnowledgeHit]:
        """Weighted FTS, falling back to trigram on the title.

        ``limit`` defaults to 3 rather than 10: these hits go straight into an
        LLM prompt, and three focused articles produce a better answer than ten
        marginal ones for a fraction of the input tokens.
        """
        cleaned = query.strip()
        if not cleaned:
            return []

        # regconfig cast: no websearch_to_tsquery(text, text) overload exists.
        tsquery = func.websearch_to_tsquery(cast(literal("english"), REGCONFIG), cleaned)
        # ts_rank's weights arg is real[]; a bare text literal has no matching
        # overload, so cast the '{...}' string to real[].
        rank = func.ts_rank(
            cast(literal(TS_RANK_WEIGHTS), ARRAY(REAL())),
            Knowledge.search_doc,
            tsquery,
        )

        stmt = (
            select(Knowledge, rank.label("rank"))
            .where(
                Knowledge.business_id == self.business_id,
                Knowledge.search_doc.op("@@")(tsquery),
            )
            .order_by(rank.desc())
            .limit(limit)
        )
        if published_only:
            stmt = stmt.where(Knowledge.status == KnowledgeStatus.PUBLISHED)

        rows = (await self.session.execute(stmt)).all()
        if rows:
            return [
                KnowledgeHit(article=row[0], rank=float(row[1]), matched_by="fts")
                for row in rows
            ]

        return await self._search_fuzzy(
            cleaned, limit=limit, published_only=published_only
        )

    async def _search_fuzzy(
        self, query: str, *, limit: int, published_only: bool
    ) -> list[KnowledgeHit]:
        """Typo path: "launchr eror" should still reach the launcher article."""
        similarity = func.similarity(Knowledge.title, query)

        stmt = (
            select(Knowledge, similarity.label("similarity"))
            .where(
                Knowledge.business_id == self.business_id,
                similarity > TRGM_THRESHOLD,
            )
            .order_by(similarity.desc())
            .limit(limit)
        )
        if published_only:
            stmt = stmt.where(Knowledge.status == KnowledgeStatus.PUBLISHED)

        rows = (await self.session.execute(stmt)).all()
        return [
            KnowledgeHit(article=row[0], rank=float(row[1]), matched_by="trigram")
            for row in rows
        ]

    async def list_published(
        self, *, limit: int = 100, offset: int = 0
    ) -> Sequence[Knowledge]:
        stmt = (
            self._scoped()
            .where(Knowledge.status == KnowledgeStatus.PUBLISHED)
            .order_by(Knowledge.title)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()
