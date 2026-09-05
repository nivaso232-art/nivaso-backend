"""Admin API — knowledge base management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import get_limit, resolve
from app.models.business import Business
from app.models.enums import KnowledgeStatus
from app.repositories.entitlements import EntitlementRepository
from app.models.knowledge import Knowledge
from app.repositories.knowledge import KnowledgeRepository

router = APIRouter(prefix="/{slug}/knowledge", tags=["admin:knowledge"])


# -- schemas ------------------------------------------------------------------

class KnowledgeOut(BaseModel):
    id: str
    title: str
    content: str
    source: str | None
    keywords: list[str]
    status: str

    @classmethod
    def from_orm(cls, k: Knowledge) -> "KnowledgeOut":
        return cls(
            id=str(k.id),
            title=k.title,
            content=k.content,
            source=k.source,
            keywords=list(k.keywords or []),
            status=k.status.value,
        )


class CreateKnowledgeIn(BaseModel):
    title: str
    content: str
    source: str | None = None
    keywords: list[str] = []
    status: KnowledgeStatus = KnowledgeStatus.PUBLISHED


class UpdateKnowledgeIn(BaseModel):
    title: str | None = None
    content: str | None = None
    source: str | None = None
    keywords: list[str] | None = None
    status: KnowledgeStatus | None = None


# -- routes -------------------------------------------------------------------

@router.get("", response_model=list[KnowledgeOut])
async def list_articles(
    slug: str,
    limit: int = 50,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeOut]:
    repo = KnowledgeRepository(session, business.id)
    articles = await repo.list_published(limit=limit)
    return [KnowledgeOut.from_orm(a) for a in articles]


@router.post("", response_model=KnowledgeOut, status_code=status.HTTP_201_CREATED)
async def create_article(
    slug: str,
    body: CreateKnowledgeIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeOut:
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(business.id)
    limit = get_limit(resolve(ent.plan, ent.overrides), FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT)
    if limit is not None:
        repo = KnowledgeRepository(session, business.id)
        count = await repo.count()
        if count >= limit:
            raise ForbiddenError(
                f"Knowledge article limit reached ({limit}). Upgrade your plan to add more.",
                details={"limit": limit, "current": count, "flag": FeatureFlag.KNOWLEDGE_ARTICLES_LIMIT},
            )
    article = Knowledge(
        title=body.title,
        content=body.content,
        source=body.source,
        keywords=body.keywords,
        status=body.status,
    )
    async with UnitOfWork(session):
        repo = KnowledgeRepository(session, business.id)
        await repo.add(article)
    return KnowledgeOut.from_orm(article)


@router.get("/{article_id}", response_model=KnowledgeOut)
async def get_article(
    slug: str,
    article_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeOut:
    import uuid
    from app.core.errors import ValidationError
    try:
        aid = uuid.UUID(article_id)
    except ValueError:
        raise ValidationError("article_id must be a valid UUID.", details={"article_id": article_id})
    repo = KnowledgeRepository(session, business.id)
    article = await repo.get(aid)
    if article is None:
        raise NotFoundError("Article not found.", details={"id": article_id})
    return KnowledgeOut.from_orm(article)


@router.patch("/{article_id}", response_model=KnowledgeOut)
async def update_article(
    slug: str,
    article_id: str,
    body: UpdateKnowledgeIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeOut:
    import uuid
    from app.core.errors import ValidationError
    try:
        aid = uuid.UUID(article_id)
    except ValueError:
        raise ValidationError("article_id must be a valid UUID.", details={"article_id": article_id})
    repo = KnowledgeRepository(session, business.id)
    article = await repo.get(aid)
    if article is None:
        raise NotFoundError("Article not found.", details={"id": article_id})

    async with UnitOfWork(session):
        if body.title is not None:
            article.title = body.title
        if body.content is not None:
            article.content = body.content
        if body.source is not None:
            article.source = body.source
        if body.keywords is not None:
            article.keywords = body.keywords
        if body.status is not None:
            article.status = body.status

    return KnowledgeOut.from_orm(article)


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(
    slug: str,
    article_id: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    import uuid
    from app.core.errors import ValidationError
    try:
        aid = uuid.UUID(article_id)
    except ValueError:
        raise ValidationError("article_id must be a valid UUID.", details={"article_id": article_id})
    repo = KnowledgeRepository(session, business.id)
    article = await repo.get(aid)
    if article is None:
        raise NotFoundError("Article not found.", details={"id": article_id})
    async with UnitOfWork(session):
        article.status = KnowledgeStatus.ARCHIVED
