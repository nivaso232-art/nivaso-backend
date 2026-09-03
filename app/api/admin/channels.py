"""Admin API — per-business channel configuration.

Each PUT checks the business's plan entitlement before allowing the write.
A business on the free tier cannot configure WhatsApp even if they supply
valid credentials — they must upgrade or have the flag granted by super-admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_business, get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.core.uow import UnitOfWork
from app.entitlements.flags import FeatureFlag
from app.entitlements.resolver import check, resolve
from app.models.business import Business
from app.models.business_channel import BusinessChannel
from app.repositories.business_channels import BusinessChannelRepository
from app.repositories.entitlements import EntitlementRepository

router = APIRouter(prefix="/{slug}/channels", tags=["admin:channels"])


# ── Entitlement guard ─────────────────────────────────────────────────────────

async def _require_channel(
    session: AsyncSession, business: Business, flag: str
) -> None:
    """Raise 403 if this channel is not in the business's plan."""
    ent_repo = EntitlementRepository(session)
    ent = await ent_repo.get_or_create(business.id)
    resolved = resolve(ent.plan, ent.overrides)
    if not check(resolved, flag):
        raise ForbiddenError(
            "This channel is not included in your current plan.",
            details={"flag": flag, "plan": ent.plan},
        )


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChannelOut(BaseModel):
    channel_type: str
    external_channel_id: str
    is_active: bool
    configured: bool
    webhook_url_path: str

    @classmethod
    def from_orm(cls, ch: BusinessChannel, slug: str) -> "ChannelOut":
        if ch.channel_type == "telegram":
            path = f"/webhooks/telegram/{slug}"
        elif ch.channel_type == "razorpay":
            path = f"/webhooks/razorpay/{slug}"
        else:
            path = "/webhooks/whatsapp"
        return cls(
            channel_type=ch.channel_type,
            external_channel_id=ch.external_channel_id,
            is_active=ch.is_active,
            configured=bool(ch.credentials),
            webhook_url_path=path,
        )


class TelegramChannelIn(BaseModel):
    bot_token: str
    webhook_secret: str = ""


class WhatsAppChannelIn(BaseModel):
    phone_number_id: str
    access_token: str
    app_secret: str = ""
    verify_token: str = ""


class RazorpayChannelIn(BaseModel):
    key_id: str
    key_secret: str
    webhook_secret: str = ""


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ChannelOut])
async def list_channels(
    slug: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> list[ChannelOut]:
    repo = BusinessChannelRepository(session)
    channels = await repo.list_for_business(business.id)
    return [ChannelOut.from_orm(ch, slug) for ch in channels]


@router.put("/telegram", response_model=ChannelOut, status_code=status.HTTP_200_OK)
async def configure_telegram(
    slug: str,
    body: TelegramChannelIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ChannelOut:
    await _require_channel(session, business, FeatureFlag.CHANNEL_TELEGRAM)
    bot_id = body.bot_token.split(":")[0] if ":" in body.bot_token else body.bot_token
    credentials = {"bot_token": body.bot_token, "webhook_secret": body.webhook_secret}
    repo = BusinessChannelRepository(session)
    async with UnitOfWork(session):
        channel = await repo.upsert(
            business_id=business.id,
            channel_type="telegram",
            external_channel_id=bot_id,
            credentials=credentials,
        )
    return ChannelOut.from_orm(channel, slug)


@router.put("/whatsapp", response_model=ChannelOut, status_code=status.HTTP_200_OK)
async def configure_whatsapp(
    slug: str,
    body: WhatsAppChannelIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ChannelOut:
    await _require_channel(session, business, FeatureFlag.CHANNEL_WHATSAPP)
    credentials = {
        "phone_number_id": body.phone_number_id,
        "access_token": body.access_token,
        "app_secret": body.app_secret,
        "verify_token": body.verify_token,
    }
    repo = BusinessChannelRepository(session)
    async with UnitOfWork(session):
        channel = await repo.upsert(
            business_id=business.id,
            channel_type="whatsapp",
            external_channel_id=body.phone_number_id,
            credentials=credentials,
        )
    return ChannelOut.from_orm(channel, slug)


@router.put("/razorpay", response_model=ChannelOut, status_code=status.HTTP_200_OK)
async def configure_razorpay(
    slug: str,
    body: RazorpayChannelIn,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> ChannelOut:
    await _require_channel(session, business, FeatureFlag.CHANNEL_PAYMENTS)
    credentials = {
        "key_id": body.key_id,
        "key_secret": body.key_secret,
        "webhook_secret": body.webhook_secret,
    }
    repo = BusinessChannelRepository(session)
    async with UnitOfWork(session):
        channel = await repo.upsert(
            business_id=business.id,
            channel_type="razorpay",
            external_channel_id=body.key_id,
            credentials=credentials,
        )
    return ChannelOut.from_orm(channel, slug)


@router.delete("/{channel_type}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_channel(
    slug: str,
    channel_type: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = BusinessChannelRepository(session)
    async with UnitOfWork(session):
        deleted = await repo.delete(business.id, channel_type)
    if not deleted:
        raise NotFoundError(
            f"No {channel_type} channel configured for this business.",
            details={"channel_type": channel_type},
        )
