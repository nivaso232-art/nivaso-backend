"""Per-business knowledge base for support answers.

No ``embedding`` column and no pgvector. Retrieval works like this:

    customer: "Bro game launch aagala"
        -> the model translates to English keywords (instructed in the system
           prompt) and calls search_knowledge("game launcher not starting")
        -> weighted ts_rank over search_doc
        -> pg_trgm similarity on title as a fallback when FTS finds nothing

The translation step is what an embedding model would otherwise have done.
Doing it in the LLM call we are already making costs nothing extra and handles
Tanglish, code-switching, and typos better than English FTS on raw input.

``keywords`` is the tuning knob: add the Tanglish and slang terms customers
actually use ("launch aagala", "open aagala", "crash") so a raw-input search
can hit even without the translation step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ARRAY, Computed, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin, pg_enum
from app.models.enums import KnowledgeStatus

if TYPE_CHECKING:
    from app.models.business import Business

# A = title, B = curated keywords, C = body.
# Keywords outrank the body so a deliberately tagged article beats one that
# merely happens to mention the word in passing.
_KNOWLEDGE_SEARCH_DOC = """
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(nivaso_array_to_text(keywords::text[]), '')), 'B') ||
    setweight(to_tsvector('english', coalesce(content, '')), 'C')
"""


class Knowledge(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "knowledge"
    # GIN on search_doc and the trigram index on title are created in
    # migration 0002.

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Where this came from: 'manual', 'faq_import', a URL, a doc name.
    source: Mapped[str | None] = mapped_column(String(255))

    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )

    status: Mapped[KnowledgeStatus] = mapped_column(
        pg_enum(KnowledgeStatus, "knowledge_status"),
        nullable=False,
        server_default=KnowledgeStatus.PUBLISHED.value,
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    search_doc: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_KNOWLEDGE_SEARCH_DOC, persisted=True)
    )

    business: Mapped[Business] = relationship(back_populates="knowledge_articles")

    @property
    def is_searchable(self) -> bool:
        return self.status is KnowledgeStatus.PUBLISHED
