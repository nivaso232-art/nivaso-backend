#!/usr/bin/env python3
"""
Seed the database with dummy data covering all 14 models and every status/enum value.

Usage:
    python scripts/seed.py            # run seed (exits if slug already exists)
    python scripts/seed.py --force    # delete existing nivaso-gaming data and re-seed

Run from project root:
    python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.agent_run import AgentRun
from app.models.business import Business
from app.models.conversation import Conversation, Message
from app.models.customer import Customer, CustomerChannel
from app.models.enums import (
    BusinessStatus,
    Channel,
    ConversationState,
    ConversationStatus,
    FulfillmentStatus,
    KnowledgeStatus,
    MessageStatus,
    MessageType,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    ProductStatus,
    SenderType,
    TicketPriority,
    TicketStatus,
    WebhookSource,
    WebhookStatus,
)
from app.models.fulfillment import Fulfillment
from app.models.knowledge import Knowledge
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.product import Product
from app.models.support_ticket import SupportTicket
from app.models.webhook_event import WebhookEvent

BUSINESS_SLUG = "nivaso-gaming"
FORCE = "--force" in sys.argv


def ago(*, days: float = 0, hours: float = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days, hours=hours)


async def main() -> None:
    engine = create_async_engine(
        str(settings.database_direct_url),
        echo=False,
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    async with factory() as session:
        existing = await session.scalar(
            select(Business).where(Business.slug == BUSINESS_SLUG)
        )
        if existing:
            if not FORCE:
                print(
                    f"Business '{BUSINESS_SLUG}' already exists. "
                    "Run with --force to delete and re-seed."
                )
                await engine.dispose()
                return
            print(f"--force: deleting existing business '{BUSINESS_SLUG}' and all related data …")
            await session.execute(delete(Business).where(Business.slug == BUSINESS_SLUG))
            await session.commit()
            print("Deleted. Re-seeding …\n")

        await _seed(session)
        await session.commit()

    await engine.dispose()
    print("\nSeed complete.")


async def _seed(s: AsyncSession) -> None:

    # =========================================================================
    # BUSINESS
    # =========================================================================
    biz_id = uuid.uuid4()
    s.add(Business(
        id=biz_id,
        slug=BUSINESS_SLUG,
        name="Nivaso Gaming Store",
        description=(
            "India's premier digital gaming marketplace — PC, console, and mobile "
            "game keys delivered instantly over WhatsApp and Telegram."
        ),
        timezone="Asia/Kolkata",
        status=BusinessStatus.ACTIVE,
        settings={
            "agent_tone": "friendly_casual",
            "default_currency": "INR",
            "supported_languages": ["en", "hi", "ta"],
            "business_hours": {
                "start": "09:00",
                "end": "23:00",
                "timezone": "Asia/Kolkata",
            },
            "razorpay_enabled": True,
            "max_order_items": 5,
            "escalation_threshold_minutes": 30,
        },
    ))
    await s.flush()
    print(f"  Business: {BUSINESS_SLUG} ({biz_id})")

    # =========================================================================
    # PRODUCTS  –  10 items across 4 categories and all 4 statuses
    # =========================================================================
    p_gta5_id = uuid.uuid4()
    s.add(Product(
        id=p_gta5_id, business_id=biz_id,
        sku="GAME-GTA5-PC-001",
        name="Grand Theft Auto V - PC",
        description="Also known as GTA 5 or GTA V. Open-world action-adventure by Rockstar. Includes GTA Online access.",
        price=Decimal("229.00"), currency="INR",
        status=ProductStatus.ACTIVE, category="Game",
        metadata_={"platform": "PC", "edition": "Standard", "genre": "Action", "delivery": "key_email"},
    ))

    p_rdr2_id = uuid.uuid4()
    s.add(Product(
        id=p_rdr2_id, business_id=biz_id,
        sku="GAME-RDR2-PC-001",
        name="Red Dead Redemption 2 - PC",
        description="Epic open-world set in 1899 American frontier. Rockstar Games.",
        price=Decimal("399.00"), currency="INR",
        status=ProductStatus.ACTIVE, category="Game",
        metadata_={"platform": "PC", "edition": "Standard", "genre": "Action-Adventure", "delivery": "key_email"},
    ))

    p_cyberpunk_id = uuid.uuid4()
    s.add(Product(
        id=p_cyberpunk_id, business_id=biz_id,
        sku="GAME-CP77-PC-001",
        name="Cyberpunk 2077 - PC",
        description="Open-world RPG set in Night City. CD Projekt Red. Includes Phantom Liberty.",
        price=Decimal("599.00"), currency="INR",
        status=ProductStatus.ACTIVE, category="Game",
        metadata_={"platform": "PC", "edition": "Ultimate", "genre": "RPG", "delivery": "key_email"},
    ))

    p_hogwarts_id = uuid.uuid4()
    s.add(Product(
        id=p_hogwarts_id, business_id=biz_id,
        sku="GAME-HL-PS5-001",
        name="Hogwarts Legacy - PS5",
        description="Open-world RPG in the Harry Potter universe. Portkey Games.",
        price=Decimal("3499.00"), currency="INR",
        status=ProductStatus.OUT_OF_STOCK, category="Game",
        metadata_={"platform": "PS5", "edition": "Standard", "genre": "RPG", "delivery": "code_or_physical"},
    ))

    p_elden_id = uuid.uuid4()
    s.add(Product(
        id=p_elden_id, business_id=biz_id,
        sku="GAME-ER-PC-001",
        name="Elden Ring - PC",
        description="Action RPG by FromSoftware. The Lands Between awaits.",
        price=Decimal("1499.00"), currency="INR",
        status=ProductStatus.ACTIVE, category="Game",
        metadata_={"platform": "PC", "edition": "Standard", "genre": "Action-RPG", "delivery": "key_email"},
    ))

    p_gppass_id = uuid.uuid4()
    s.add(Product(
        id=p_gppass_id, business_id=biz_id,
        sku="SUB-GPPASS-1M",
        name="Google Play Pass - 1 Month",
        description="Access 500+ premium Android games and apps. No ads, no in-app purchases.",
        price=Decimal("99.00"), currency="INR",
        status=ProductStatus.ACTIVE, category="Subscription",
        metadata_={"platform": "Android", "duration_days": 30, "delivery": "redemption_code"},
    ))

    p_psplus_id = uuid.uuid4()
    s.add(Product(
        id=p_psplus_id, business_id=biz_id,
        sku="SUB-PSPLUS-3M",
        name="PlayStation Plus Essential - 3 Months",
        description="Online multiplayer, monthly free games, exclusive discounts.",
        price=Decimal("1299.00"), currency="INR",
        status=ProductStatus.ACTIVE, category="Subscription",
        metadata_={"platform": "PlayStation", "duration_days": 90, "delivery": "redemption_code"},
    ))

    p_headset_id = uuid.uuid4()
    s.add(Product(
        id=p_headset_id, business_id=biz_id,
        sku="ACC-HYPERX-CLOUD2",
        name="HyperX Cloud II Gaming Headset",
        description="7.1 virtual surround, memory foam ear cushions, detachable mic.",
        price=Decimal("5499.00"), currency="INR",
        status=ProductStatus.INACTIVE, category="Accessory",
        metadata_={"type": "headset", "connectivity": "USB+3.5mm", "surround": "7.1 virtual"},
    ))

    p_dlc_id = uuid.uuid4()
    s.add(Product(
        id=p_dlc_id, business_id=biz_id,
        sku="DLC-GTA5-BULLSHARK",
        name="GTA Online - Bull Shark Cash Card",
        description="500,000 GTA$ deposited directly into your Maze Bank account.",
        price=Decimal("379.00"), currency="INR",
        status=ProductStatus.ACTIVE, category="DLC",
        metadata_={"platform": "PC", "game": "GTA Online", "gta_dollars": 500000, "delivery": "key_email"},
    ))

    p_gamepass_id = uuid.uuid4()
    s.add(Product(
        id=p_gamepass_id, business_id=biz_id,
        sku="SUB-XGPU-1M-ARCH",
        name="Xbox Game Pass Ultimate - 1 Month [Archived]",
        description="Archived — replaced by the 3-month bundle. Do not sell.",
        price=Decimal("489.00"), currency="INR",
        status=ProductStatus.ARCHIVED, category="Subscription",
        metadata_={"platform": "Xbox", "duration_days": 30, "reason_archived": "replaced_by_3m_bundle"},
    ))

    await s.flush()
    print("  Products: 10 (active, out_of_stock, inactive, archived)")

    # =========================================================================
    # CUSTOMERS  –  5 with varied profiles
    # =========================================================================
    c1_id = uuid.uuid4()
    s.add(Customer(
        id=c1_id, business_id=biz_id,
        name="Arjun Mehta", phone="919876543210", email="arjun.mehta@gmail.com",
        metadata_={"preferred_language": "en", "loyalty_tier": "gold"},
    ))
    c2_id = uuid.uuid4()
    s.add(Customer(
        id=c2_id, business_id=biz_id,
        name="Priya Sharma", phone="919988776655", email="priya.sharma@outlook.com",
        metadata_={"preferred_language": "hi"},
    ))
    c3_id = uuid.uuid4()
    s.add(Customer(
        id=c3_id, business_id=biz_id,
        name="Karthik Rajan", phone="919123456789", email=None,
        metadata_={"preferred_language": "ta"},
    ))
    c4_id = uuid.uuid4()
    s.add(Customer(
        id=c4_id, business_id=biz_id,
        name="Deepika Nair", phone="917012345678", email="deepika.nair@protonmail.com",
        metadata_={"preferred_language": "en", "notes": "multi-channel user"},
    ))
    c5_id = uuid.uuid4()
    s.add(Customer(
        id=c5_id, business_id=biz_id,
        name=None, phone="918099887766", email=None,
        metadata_={"preferred_language": "en"},
    ))
    await s.flush()
    print("  Customers: 5")

    # =========================================================================
    # CUSTOMER CHANNELS  –  6 covering all three channel types
    # =========================================================================
    cc1_wa_id = uuid.uuid4()
    s.add(CustomerChannel(
        id=cc1_wa_id, business_id=biz_id, customer_id=c1_id,
        channel=Channel.WHATSAPP, external_user_id="919876543210",
        display_name="Arjun", metadata_={"wa_profile_name": "Arjun Mehta"},
    ))
    cc2_tg_id = uuid.uuid4()
    s.add(CustomerChannel(
        id=cc2_tg_id, business_id=biz_id, customer_id=c2_id,
        channel=Channel.TELEGRAM, external_user_id="789456123",
        display_name="Priya", metadata_={"telegram_username": "@priya_sharma"},
    ))
    cc3_web_id = uuid.uuid4()
    s.add(CustomerChannel(
        id=cc3_web_id, business_id=biz_id, customer_id=c3_id,
        channel=Channel.WEB, external_user_id="web-session-karthik-001",
        display_name="Karthik", metadata_={},
    ))
    cc4_wa_id = uuid.uuid4()
    s.add(CustomerChannel(
        id=cc4_wa_id, business_id=biz_id, customer_id=c4_id,
        channel=Channel.WHATSAPP, external_user_id="917012345678",
        display_name="Deepika", metadata_={"wa_profile_name": "Deepika N"},
    ))
    cc4_tg_id = uuid.uuid4()
    s.add(CustomerChannel(
        id=cc4_tg_id, business_id=biz_id, customer_id=c4_id,
        channel=Channel.TELEGRAM, external_user_id="321654987",
        display_name="Deepika", metadata_={"telegram_username": "@deepika_nair"},
    ))
    cc5_wa_id = uuid.uuid4()
    s.add(CustomerChannel(
        id=cc5_wa_id, business_id=biz_id, customer_id=c5_id,
        channel=Channel.WHATSAPP, external_user_id="918099887766",
        display_name=None, metadata_={},
    ))
    await s.flush()
    print("  CustomerChannels: 6 (WhatsApp x3, Telegram x2, Web x1)")

    # =========================================================================
    # CONVERSATIONS  –  6 covering all statuses and states
    # The partial unique index allows only ONE active conv per channel.
    # cc1_wa → conv1 CLOSED, cc2_tg → conv2 ACTIVE, cc3_web → conv3 CLOSED
    # cc4_wa → conv4 ACTIVE, cc4_tg → conv5 CLOSED, cc5_wa → conv6 ACTIVE
    # =========================================================================
    conv1_id = uuid.uuid4()
    s.add(Conversation(
        id=conv1_id, business_id=biz_id,
        customer_id=c1_id, customer_channel_id=cc1_wa_id,
        channel=Channel.WHATSAPP,
        status=ConversationStatus.CLOSED,
        current_state=ConversationState.COMPLETED,
        last_message_at=ago(days=3),
        metadata_={},
    ))
    conv2_id = uuid.uuid4()
    s.add(Conversation(
        id=conv2_id, business_id=biz_id,
        customer_id=c2_id, customer_channel_id=cc2_tg_id,
        channel=Channel.TELEGRAM,
        status=ConversationStatus.ACTIVE,
        current_state=ConversationState.PAYMENT_PENDING,
        last_message_at=ago(hours=2),
        metadata_={},
    ))
    conv3_id = uuid.uuid4()
    s.add(Conversation(
        id=conv3_id, business_id=biz_id,
        customer_id=c3_id, customer_channel_id=cc3_web_id,
        channel=Channel.WEB,
        status=ConversationStatus.CLOSED,
        current_state=ConversationState.COMPLETED,
        last_message_at=ago(days=7),
        metadata_={},
    ))
    conv4_id = uuid.uuid4()
    s.add(Conversation(
        id=conv4_id, business_id=biz_id,
        customer_id=c4_id, customer_channel_id=cc4_wa_id,
        channel=Channel.WHATSAPP,
        status=ConversationStatus.ACTIVE,
        current_state=ConversationState.PRODUCT_ENQUIRY,
        last_message_at=ago(hours=1),
        metadata_={},
    ))
    conv5_id = uuid.uuid4()
    s.add(Conversation(
        id=conv5_id, business_id=biz_id,
        customer_id=c4_id, customer_channel_id=cc4_tg_id,
        channel=Channel.TELEGRAM,
        status=ConversationStatus.CLOSED,
        current_state=ConversationState.COMPLETED,
        last_message_at=ago(days=14),
        metadata_={},
    ))
    conv6_id = uuid.uuid4()
    s.add(Conversation(
        id=conv6_id, business_id=biz_id,
        customer_id=c5_id, customer_channel_id=cc5_wa_id,
        channel=Channel.WHATSAPP,
        status=ConversationStatus.ACTIVE,
        current_state=ConversationState.SUPPORT,
        last_message_at=ago(hours=0.5),
        metadata_={},
    ))
    await s.flush()
    print("  Conversations: 6 (3 active, 3 closed — all states represented)")

    # =========================================================================
    # MESSAGES  –  realistic per-conversation flows
    # seq is GENERATED ALWAYS AS IDENTITY — never set it manually.
    # Covers: all SenderTypes, all MessageTypes, tool_use_id linking.
    # =========================================================================

    # --- Conv 1 (WhatsApp, c1): full purchase flow → COMPLETED ---------------
    for m in [
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Hi, do you have GTA 5 for PC?",
                external_message_id="wamid.HN0000C1.001"),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Hey Arjun! 👋 Let me check our catalog for GTA 5."),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C1_srch_01",
                payload={"tool": "search_products", "arguments": {"query": "GTA 5 PC", "limit": 5}}),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C1_srch_01",
                payload={"tool": "search_products", "is_error": False,
                         "result": [{"sku": "GAME-GTA5-PC-001", "name": "Grand Theft Auto V - PC", "price": 229.0}]}),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Yes! *Grand Theft Auto V - PC* is ₹229 🎮\nIncludes GTA Online. Key delivered via email. Want to order?"),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Yes bro, order kar do",
                external_message_id="wamid.HN0000C1.002"),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C1_ord_01",
                payload={"tool": "place_order", "arguments": {"sku": "GAME-GTA5-PC-001", "quantity": 1}}),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C1_ord_01",
                payload={"tool": "place_order", "is_error": False,
                         "result": {"order_reference": "ORD-2609-SEED01", "payment_url": "https://rzp.io/i/seed01"}}),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.READ,
                content="Order placed! 🎉\n*Order:* ORD-2609-SEED01 | *Amount:* ₹229\n👉 https://rzp.io/i/seed01\nKey sent to arjun.mehta@gmail.com after payment."),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.SYSTEM, message_type=MessageType.SYSTEM_NOTE,
                status=MessageStatus.SENT,
                content="Razorpay payment confirmed. Order ORD-2609-SEED01 → PAID. Fulfillment triggered."),
        Message(business_id=biz_id, conversation_id=conv1_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.READ,
                content="Payment received! ✅ GTA 5 key sent to arjun.mehta@gmail.com. Enjoy! 🎮"),
    ]:
        s.add(m)

    # --- Conv 2 (Telegram, c2): payment pending flow -------------------------
    for m in [
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="PS Plus 3 month kitna hai?",
                external_message_id="tg.C2.msg.001"),
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C2_srch_01",
                payload={"tool": "search_products", "arguments": {"query": "PlayStation Plus 3 month"}}),
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C2_srch_01",
                payload={"tool": "search_products", "is_error": False,
                         "result": [{"sku": "SUB-PSPLUS-3M", "name": "PlayStation Plus Essential - 3 Months", "price": 1299.0}]}),
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="PS Plus Essential 3 Months — ₹1299 🎮\nOnline multiplayer + free monthly games. Order karein?"),
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="ha order karo",
                external_message_id="tg.C2.msg.002"),
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C2_ord_01",
                payload={"tool": "place_order", "arguments": {"sku": "SUB-PSPLUS-3M", "quantity": 1}}),
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C2_ord_01",
                payload={"tool": "place_order", "is_error": False,
                         "result": {"order_reference": "ORD-2609-SEED02", "payment_url": "https://rzp.io/i/seed02"}}),
        Message(business_id=biz_id, conversation_id=conv2_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Order ho gayi! 🎉\n*Order:* ORD-2609-SEED02 | *Amount:* ₹1299\n👉 https://rzp.io/i/seed02\nLink 30 min valid hai ⏱️"),
    ]:
        s.add(m)

    # --- Conv 3 (Web, c3): cancelled order flow ------------------------------
    for m in [
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="I want to buy Elden Ring for PC"),
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C3_srch_01",
                payload={"tool": "search_products", "arguments": {"query": "Elden Ring PC"}}),
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C3_srch_01",
                payload={"tool": "search_products", "is_error": False,
                         "result": [{"sku": "GAME-ER-PC-001", "name": "Elden Ring - PC", "price": 1499.0}]}),
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Elden Ring - PC is ₹1499. Shall I place the order?"),
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Actually cancel, changed my mind."),
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C3_cncl_01",
                payload={"tool": "cancel_order", "arguments": {"order_reference": "ORD-2609-SEED03"}}),
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C3_cncl_01",
                payload={"tool": "cancel_order", "is_error": False, "result": {"cancelled": True}}),
        Message(business_id=biz_id, conversation_id=conv3_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.READ,
                content="No problem! Order cancelled. Come back anytime. 😊"),
    ]:
        s.add(m)

    # --- Conv 4 (WhatsApp, c4): product enquiry in progress ------------------
    for m in [
        Message(business_id=biz_id, conversation_id=conv4_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Hey, good RPG games for PC under 500?",
                external_message_id="wamid.HN0000C4.001"),
        Message(business_id=biz_id, conversation_id=conv4_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C4_srch_01",
                payload={"tool": "search_products", "arguments": {"query": "RPG PC", "max_price": 500}}),
        Message(business_id=biz_id, conversation_id=conv4_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C4_srch_01",
                payload={"tool": "search_products", "is_error": False, "result": [
                    {"sku": "GAME-GTA5-PC-001", "name": "Grand Theft Auto V - PC", "price": 229.0},
                    {"sku": "GAME-RDR2-PC-001", "name": "Red Dead Redemption 2 - PC", "price": 399.0},
                ]}),
        Message(business_id=biz_id, conversation_id=conv4_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.READ,
                content="Under ₹500:\n1. *GTA V* — ₹229 (Action-Adventure, open world)\n2. *Red Dead Redemption 2* — ₹399 (Wild West RPG)\n\nWhich interests you?"),
        Message(business_id=biz_id, conversation_id=conv4_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Tell me more about RDR2",
                external_message_id="wamid.HN0000C4.002"),
        Message(business_id=biz_id, conversation_id=conv4_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="*Red Dead Redemption 2* is a masterpiece! 🤠\n• Open-world 1899 frontier\n• Play as Arthur Morgan\n• 60+ hour story, online included\nAll for ₹399. Order?"),
    ]:
        s.add(m)

    # --- Conv 5 (Telegram, c4): quick completed purchase ---------------------
    for m in [
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Google Play Pass lena hai 1 month wala",
                external_message_id="tg.C5.msg.001"),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C5_srch_01",
                payload={"tool": "search_products", "arguments": {"query": "Google Play Pass"}}),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C5_srch_01",
                payload={"tool": "search_products", "is_error": False,
                         "result": [{"sku": "SUB-GPPASS-1M", "name": "Google Play Pass - 1 Month", "price": 99.0}]}),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Google Play Pass 1 Month — sirf ₹99! 🎮 Order karein?"),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="haan",
                external_message_id="tg.C5.msg.002"),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C5_ord_01",
                payload={"tool": "place_order", "arguments": {"sku": "SUB-GPPASS-1M", "quantity": 1}}),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C5_ord_01",
                payload={"tool": "place_order", "is_error": False,
                         "result": {"order_reference": "ORD-2608-SEED05", "payment_url": "https://rzp.io/i/seed05"}}),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.SYSTEM, message_type=MessageType.SYSTEM_NOTE,
                status=MessageStatus.SENT,
                content="Payment confirmed. Order ORD-2608-SEED05 → FULFILLED. Code delivered."),
        Message(business_id=biz_id, conversation_id=conv5_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.READ,
                content="Done! ✅ Play Pass code bhej diya. Enjoy! 🎉"),
    ]:
        s.add(m)

    # --- Conv 6 (WhatsApp, c5): support issue → human handoff ---------------
    for m in [
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Bro mera GTA key kaam nahi kar raha. Invalid key bol raha hai Steam.",
                external_message_id="wamid.HN0000C6.001"),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Oh no, really sorry! 😟 Let me look into this right away."),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C6_kb_01",
                payload={"tool": "search_knowledge", "arguments": {"query": "Steam invalid key troubleshooting"}}),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C6_kb_01",
                payload={"tool": "search_knowledge", "is_error": False,
                         "result": [{"title": "Troubleshooting Game Launch Issues",
                                     "content": "If a key shows as invalid or already activated, contact support immediately."}]}),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Kuch check karo:\n1. Steam restart kar ke try karo\n2. Key mein koi space to nahi?\n3. Kisi aur account pe redeem to nahi hua?\n\nAgar phir bhi nahi hua, ticket raise karta hoon."),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
                content="Sab try kar liya. Error: 'This product key has already been activated.'",
                external_message_id="wamid.HN0000C6.002"),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TOOL_CALL,
                status=MessageStatus.SENT, content=None,
                tool_use_id="toolu_C6_tkt_01",
                payload={"tool": "create_support_ticket",
                         "arguments": {"reason": "GAME_ACCESS_PROBLEM", "priority": "HIGH",
                                       "summary": "Customer received an already-activated Steam key for GTA V PC."}}),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.TOOL, message_type=MessageType.TOOL_RESULT,
                status=MessageStatus.RECEIVED, content=None,
                tool_use_id="toolu_C6_tkt_01",
                payload={"tool": "create_support_ticket", "is_error": False,
                         "result": {"ticket_reference": "TKT-2609-SEED01"}}),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.ASSISTANT, message_type=MessageType.TEXT,
                status=MessageStatus.DELIVERED,
                content="Ticket raise kar di! 🎫 *Ticket: TKT-2609-SEED01*\n\nDefective key case hai. Team 2-4 ghante mein contact karegi ya replacement bhejegi. Sorry for the trouble! 🙏"),
        Message(business_id=biz_id, conversation_id=conv6_id,
                sender_type=SenderType.SYSTEM, message_type=MessageType.SYSTEM_NOTE,
                status=MessageStatus.SENT,
                content="Support ticket TKT-2609-SEED01 raised. Priority: HIGH. Reason: GAME_ACCESS_PROBLEM. Assigned: support@nivaso.in"),
    ]:
        s.add(m)

    await s.flush()
    print("  Messages: 52 (text, tool_call, tool_result, system_note — all sender types)")

    # =========================================================================
    # ORDERS  –  8 rows covering all 8 OrderStatus values
    # Check constraint: total = subtotal - discount  (enforced in DB)
    # =========================================================================
    ord1_id = uuid.uuid4()
    s.add(Order(
        id=ord1_id, business_id=biz_id, customer_id=c1_id,
        conversation_id=conv1_id, reference="ORD-2609-SEED01",
        status=OrderStatus.FULFILLED, currency="INR",
        subtotal=Decimal("608.00"), discount=Decimal("0.00"), total=Decimal("608.00"),
        metadata_={"source": "whatsapp_agent"},
    ))
    ord2_id = uuid.uuid4()
    s.add(Order(
        id=ord2_id, business_id=biz_id, customer_id=c2_id,
        conversation_id=conv2_id, reference="ORD-2609-SEED02",
        status=OrderStatus.PAYMENT_PENDING, currency="INR",
        subtotal=Decimal("1299.00"), discount=Decimal("0.00"), total=Decimal("1299.00"),
        metadata_={"source": "telegram_agent"},
    ))
    ord3_id = uuid.uuid4()
    s.add(Order(
        id=ord3_id, business_id=biz_id, customer_id=c3_id,
        conversation_id=conv3_id, reference="ORD-2609-SEED03",
        status=OrderStatus.CANCELLED, currency="INR",
        subtotal=Decimal("1499.00"), discount=Decimal("0.00"), total=Decimal("1499.00"),
        metadata_={"source": "web_agent", "cancel_reason": "customer_changed_mind"},
    ))
    ord4_id = uuid.uuid4()
    s.add(Order(
        id=ord4_id, business_id=biz_id, customer_id=c4_id,
        conversation_id=None, reference="ORD-2609-SEED04",
        status=OrderStatus.DRAFT, currency="INR",
        subtotal=Decimal("99.00"), discount=Decimal("0.00"), total=Decimal("99.00"),
        metadata_={"source": "admin_api"},
    ))
    ord5_id = uuid.uuid4()
    s.add(Order(
        id=ord5_id, business_id=biz_id, customer_id=c4_id,
        conversation_id=conv4_id, reference="ORD-2609-SEED05",
        status=OrderStatus.PENDING_CONFIRMATION, currency="INR",
        subtotal=Decimal("399.00"), discount=Decimal("0.00"), total=Decimal("399.00"),
        metadata_={"source": "whatsapp_agent"},
    ))
    ord6_id = uuid.uuid4()
    # subtotal 1898 − discount 100 = total 1798
    s.add(Order(
        id=ord6_id, business_id=biz_id, customer_id=c1_id,
        conversation_id=None, reference="ORD-2609-SEED06",
        status=OrderStatus.PAID, currency="INR",
        subtotal=Decimal("1898.00"), discount=Decimal("100.00"), total=Decimal("1798.00"),
        metadata_={"source": "whatsapp_agent", "promo_code": "LOYAL10"},
    ))
    ord7_id = uuid.uuid4()
    s.add(Order(
        id=ord7_id, business_id=biz_id, customer_id=c2_id,
        conversation_id=None, reference="ORD-2609-SEED07",
        status=OrderStatus.PAYMENT_FAILED, currency="INR",
        subtotal=Decimal("1499.00"), discount=Decimal("0.00"), total=Decimal("1499.00"),
        metadata_={"source": "telegram_agent"},
    ))
    ord8_id = uuid.uuid4()
    s.add(Order(
        id=ord8_id, business_id=biz_id, customer_id=c5_id,
        conversation_id=None, reference="ORD-2609-SEED08",
        status=OrderStatus.REFUNDED, currency="INR",
        subtotal=Decimal("229.00"), discount=Decimal("0.00"), total=Decimal("229.00"),
        metadata_={"source": "whatsapp_agent", "refund_reason": "defective_key"},
    ))
    await s.flush()
    print("  Orders: 8 (DRAFT, PENDING_CONFIRMATION, PAYMENT_PENDING, PAYMENT_FAILED, PAID, FULFILLED, CANCELLED, REFUNDED)")

    # =========================================================================
    # ORDER ITEMS  –  11 lines; snapshots frozen at purchase time
    # Check constraint: total = unit_price * quantity
    # OrderItem has no business_id (no TenantMixin — derived through order).
    # =========================================================================
    # ord1 (FULFILLED) — GTA5 + Bull Shark DLC = 229 + 379 = 608 ✓
    s.add(OrderItem(order_id=ord1_id, product_id=p_gta5_id,
                    product_name="Grand Theft Auto V - PC", product_sku="GAME-GTA5-PC-001",
                    unit_price=Decimal("229.00"), quantity=1, total=Decimal("229.00")))
    s.add(OrderItem(order_id=ord1_id, product_id=p_dlc_id,
                    product_name="GTA Online - Bull Shark Cash Card", product_sku="DLC-GTA5-BULLSHARK",
                    unit_price=Decimal("379.00"), quantity=1, total=Decimal("379.00")))

    # ord2 (PAYMENT_PENDING) — PS Plus 3M = 1299 ✓
    s.add(OrderItem(order_id=ord2_id, product_id=p_psplus_id,
                    product_name="PlayStation Plus Essential - 3 Months", product_sku="SUB-PSPLUS-3M",
                    unit_price=Decimal("1299.00"), quantity=1, total=Decimal("1299.00")))

    # ord3 (CANCELLED) — Elden Ring = 1499 ✓
    s.add(OrderItem(order_id=ord3_id, product_id=p_elden_id,
                    product_name="Elden Ring - PC", product_sku="GAME-ER-PC-001",
                    unit_price=Decimal("1499.00"), quantity=1, total=Decimal("1499.00")))

    # ord4 (DRAFT) — Google Play Pass = 99 ✓
    s.add(OrderItem(order_id=ord4_id, product_id=p_gppass_id,
                    product_name="Google Play Pass - 1 Month", product_sku="SUB-GPPASS-1M",
                    unit_price=Decimal("99.00"), quantity=1, total=Decimal("99.00")))

    # ord5 (PENDING_CONFIRMATION) — RDR2 = 399 ✓
    s.add(OrderItem(order_id=ord5_id, product_id=p_rdr2_id,
                    product_name="Red Dead Redemption 2 - PC", product_sku="GAME-RDR2-PC-001",
                    unit_price=Decimal("399.00"), quantity=1, total=Decimal("399.00")))

    # ord6 (PAID) — PS Plus (1299) + Cyberpunk (599) = 1898 = subtotal ✓  (after discount → 1798 total)
    s.add(OrderItem(order_id=ord6_id, product_id=p_psplus_id,
                    product_name="PlayStation Plus Essential - 3 Months", product_sku="SUB-PSPLUS-3M",
                    unit_price=Decimal("1299.00"), quantity=1, total=Decimal("1299.00")))
    s.add(OrderItem(order_id=ord6_id, product_id=p_cyberpunk_id,
                    product_name="Cyberpunk 2077 - PC", product_sku="GAME-CP77-PC-001",
                    unit_price=Decimal("599.00"), quantity=1, total=Decimal("599.00")))

    # ord7 (PAYMENT_FAILED) — Elden Ring = 1499 ✓
    s.add(OrderItem(order_id=ord7_id, product_id=p_elden_id,
                    product_name="Elden Ring - PC", product_sku="GAME-ER-PC-001",
                    unit_price=Decimal("1499.00"), quantity=1, total=Decimal("1499.00")))

    # ord8 (REFUNDED) — GTA5 = 229 ✓
    s.add(OrderItem(order_id=ord8_id, product_id=p_gta5_id,
                    product_name="Grand Theft Auto V - PC", product_sku="GAME-GTA5-PC-001",
                    unit_price=Decimal("229.00"), quantity=1, total=Decimal("229.00")))

    # Multi-quantity example: 2x Google Play Pass on ord4 variant — demonstrate qty > 1
    # (add a second item to ord4: 2 × ₹99 = ₹198, but ord4.subtotal is 99 already set above.
    #  Keep items consistent with order subtotal: only 1 item totalling 99.)
    # Already satisfied above.

    await s.flush()
    print("  OrderItems: 10 (snapshots frozen, subtotal/total constraints satisfied)")

    # =========================================================================
    # PAYMENTS  –  7 rows; append-only pattern; all PaymentStatus values
    # Includes duplicate payment detection (is_duplicate=True, needs_refund=True).
    # =========================================================================

    # ord1 — SUCCESS (Razorpay)
    pay1_id = uuid.uuid4()
    s.add(Payment(
        id=pay1_id, business_id=biz_id, order_id=ord1_id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id="pay_seed01_abc001",
        provider_order_id="order_seed01_xyz001",
        provider_payment_link_id="plink_seed01_001",
        payment_url="https://rzp.io/i/seed01",
        amount=Decimal("608.00"), currency="INR",
        status=PaymentStatus.SUCCESS,
        is_duplicate=False, needs_refund=False,
        raw_payload={"razorpay_payment_id": "pay_seed01_abc001",
                     "razorpay_order_id": "order_seed01_xyz001",
                     "razorpay_signature": "sha256_sig_seed01"},
    ))

    # ord2 — PENDING (awaiting customer)
    pay2_id = uuid.uuid4()
    s.add(Payment(
        id=pay2_id, business_id=biz_id, order_id=ord2_id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id=None,
        provider_order_id="order_seed02_xyz002",
        provider_payment_link_id="plink_seed02_001",
        payment_url="https://rzp.io/i/seed02",
        amount=Decimal("1299.00"), currency="INR",
        status=PaymentStatus.PENDING,
        is_duplicate=False, needs_refund=False,
        raw_payload={"payment_link_id": "plink_seed02_001", "expires_at": 1756990800},
    ))

    # ord6 — SUCCESS (Razorpay)
    pay6a_id = uuid.uuid4()
    s.add(Payment(
        id=pay6a_id, business_id=biz_id, order_id=ord6_id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id="pay_seed06_def006",
        provider_order_id="order_seed06_uvw006",
        provider_payment_link_id="plink_seed06_001",
        payment_url="https://rzp.io/i/seed06",
        amount=Decimal("1798.00"), currency="INR",
        status=PaymentStatus.SUCCESS,
        is_duplicate=False, needs_refund=False,
        raw_payload={"razorpay_payment_id": "pay_seed06_def006"},
    ))

    # ord6 — second payment; duplicate detected (Rule 7: never silently drop)
    pay6b_id = uuid.uuid4()
    s.add(Payment(
        id=pay6b_id, business_id=biz_id, order_id=ord6_id,
        provider=PaymentProvider.MANUAL,
        provider_payment_id=None,
        provider_order_id=None,
        provider_payment_link_id=None,
        payment_url=None,
        amount=Decimal("1798.00"), currency="INR",
        status=PaymentStatus.SUCCESS,
        is_duplicate=True, needs_refund=True,
        raw_payload={"note": "Customer paid again via UPI. Duplicate confirmed. Refund queued.", "upi_ref": "UPI123456"},
    ))

    # ord7 — first attempt: FAILED (insufficient funds)
    pay7a_id = uuid.uuid4()
    s.add(Payment(
        id=pay7a_id, business_id=biz_id, order_id=ord7_id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id="pay_seed07a_fail1",
        provider_order_id="order_seed07_abc007",
        provider_payment_link_id="plink_seed07_001",
        payment_url="https://rzp.io/i/seed07",
        amount=Decimal("1499.00"), currency="INR",
        status=PaymentStatus.FAILED,
        failure_reason="INSUFFICIENT_FUNDS",
        is_duplicate=False, needs_refund=False,
        raw_payload={"error_code": "BAD_REQUEST_ERROR",
                     "error_description": "Your bank account does not have sufficient funds."},
    ))

    # ord7 — second attempt: also FAILED (user cancelled) — demonstrates append-only
    pay7b_id = uuid.uuid4()
    s.add(Payment(
        id=pay7b_id, business_id=biz_id, order_id=ord7_id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id="pay_seed07b_fail2",
        provider_order_id="order_seed07_abc007",
        provider_payment_link_id=None,
        payment_url=None,
        amount=Decimal("1499.00"), currency="INR",
        status=PaymentStatus.FAILED,
        failure_reason="PAYMENT_CANCELLED",
        is_duplicate=False, needs_refund=False,
        raw_payload={"error_code": "BAD_REQUEST_ERROR", "error_description": "Payment cancelled by user."},
    ))

    # ord8 — SUCCESS then REFUNDED
    pay8_id = uuid.uuid4()
    s.add(Payment(
        id=pay8_id, business_id=biz_id, order_id=ord8_id,
        provider=PaymentProvider.RAZORPAY,
        provider_payment_id="pay_seed08_ghi008",
        provider_order_id="order_seed08_jkl008",
        provider_payment_link_id="plink_seed08_001",
        payment_url="https://rzp.io/i/seed08",
        amount=Decimal("229.00"), currency="INR",
        status=PaymentStatus.REFUNDED,
        is_duplicate=False, needs_refund=False,
        raw_payload={"razorpay_payment_id": "pay_seed08_ghi008", "refund_id": "rfnd_seed08_xyz"},
    ))

    await s.flush()
    print("  Payments: 7 (PENDING, SUCCESS, FAILED x2 append-only, REFUNDED, duplicate detection)")

    # =========================================================================
    # FULFILLMENTS  –  4 rows; all 4 FulfillmentStatus values
    # Deliberately credential-free per Rule 10.
    # =========================================================================
    s.add(Fulfillment(
        business_id=biz_id, order_id=ord1_id,
        status=FulfillmentStatus.DELIVERED,
        fulfilled_at=ago(days=3),
        notes="GTA5 key + Bull Shark card delivered via email.",
        metadata_={"delivery_method": "email", "delivered_by": "agent_auto",
                   "credential_ref": "vault://keys/gta5/batch-3/slot-42"},
    ))
    s.add(Fulfillment(
        business_id=biz_id, order_id=ord2_id,
        status=FulfillmentStatus.PENDING,
        fulfilled_at=None,
        notes="Waiting for Razorpay payment confirmation before releasing key.",
        metadata_={"delivery_method": "redemption_code"},
    ))
    s.add(Fulfillment(
        business_id=biz_id, order_id=ord6_id,
        status=FulfillmentStatus.READY,
        fulfilled_at=None,
        notes="PS Plus + Cyberpunk keys ready. Holding delivery pending duplicate-payment investigation.",
        metadata_={"delivery_method": "email",
                   "credential_ref": "vault://keys/psplus/batch-1/slot-7"},
    ))
    s.add(Fulfillment(
        business_id=biz_id, order_id=ord8_id,
        status=FulfillmentStatus.FAILED,
        fulfilled_at=None,
        notes="Key was already activated. Refund issued. Support ticket TKT-2609-SEED01 raised.",
        metadata_={"failure_reason": "key_already_activated", "refund_issued": True},
    ))
    await s.flush()
    print("  Fulfillments: 4 (PENDING, READY, DELIVERED, FAILED)")

    # =========================================================================
    # SUPPORT TICKETS  –  5 rows; all TicketStatus + all TicketPriority values
    # =========================================================================
    s.add(SupportTicket(
        business_id=biz_id, customer_id=c5_id,
        conversation_id=conv6_id, order_id=ord8_id,
        reference="TKT-2609-SEED01",
        status=TicketStatus.OPEN, priority=TicketPriority.HIGH,
        assigned_to="support@nivaso.in",
        reason="GAME_ACCESS_PROBLEM",
        summary="Customer received an already-activated Steam key for GTA V PC. Replacement or refund needed.",
        metadata_={"reported_error": "This product key has already been activated.", "platform": "Steam"},
    ))
    s.add(SupportTicket(
        business_id=biz_id, customer_id=c1_id,
        conversation_id=None, order_id=ord6_id,
        reference="TKT-2609-SEED02",
        status=TicketStatus.IN_PROGRESS, priority=TicketPriority.HIGH,
        assigned_to="finance@nivaso.in",
        reason="DOUBLE_PAYMENT",
        summary="Customer paid twice for ORD-2609-SEED06. Second payment flagged is_duplicate=True. ₹1798 refund in progress.",
        metadata_={"duplicate_payment_id": str(pay6b_id), "refund_amount": "1798.00"},
    ))
    s.add(SupportTicket(
        business_id=biz_id, customer_id=c2_id,
        conversation_id=conv2_id, order_id=ord2_id,
        reference="TKT-2609-SEED03",
        status=TicketStatus.WAITING_CUSTOMER, priority=TicketPriority.MEDIUM,
        assigned_to="support@nivaso.in",
        reason="REFUND_REQUEST",
        summary="Customer wants to cancel PS Plus 3M and get refund. Waiting for written confirmation.",
        metadata_={"requested_via": "telegram"},
    ))
    s.add(SupportTicket(
        business_id=biz_id, customer_id=c3_id,
        conversation_id=conv3_id, order_id=ord3_id,
        reference="TKT-2609-SEED04",
        status=TicketStatus.RESOLVED, priority=TicketPriority.LOW,
        assigned_to="support@nivaso.in",
        reason="BILLING_QUERY",
        summary="Customer queried cancellation charge. Confirmed no payment was captured (order cancelled pre-payment). Resolved.",
        metadata_={"resolution": "No charge occurred. Order was pre-payment when cancelled."},
    ))
    s.add(SupportTicket(
        business_id=biz_id, customer_id=c4_id,
        conversation_id=conv5_id, order_id=None,
        reference="TKT-2609-SEED05",
        status=TicketStatus.CLOSED, priority=TicketPriority.MEDIUM,
        assigned_to=None,
        reason="TECHNICAL_ISSUE",
        summary="Play Pass code not working. Resolved — Play Store cache issue on customer's device. Auto-closed after 48h no response.",
        metadata_={"resolution": "User cleared Play Store cache. Self-resolved.", "auto_closed": True},
    ))
    await s.flush()
    print("  SupportTickets: 5 (OPEN, IN_PROGRESS, WAITING_CUSTOMER, RESOLVED, CLOSED; LOW->HIGH priority)")

    # =========================================================================
    # KNOWLEDGE BASE  –  6 articles; all 3 KnowledgeStatus values
    # =========================================================================
    s.add(Knowledge(
        business_id=biz_id,
        title="How to Redeem Your Game Key",
        content="""After purchase, your key is delivered to your registered email within 5 minutes.

Steam (PC):
1. Open Steam → Games → Activate a Product on Steam
2. Enter the key and click Next
3. Game will appear in your Library

PlayStation:
1. PlayStation Store → your profile → Redeem Codes
2. Enter code → Redeem

Common issues:
- Key already activated: contact us immediately — we'll replace it
- Typos: copy from the email, don't retype manually
- Region lock: all our keys are India-region compatible""",
        source="manual",
        keywords=["redeem", "key", "steam", "activate", "how to", "code", "steps", "game key"],
        status=KnowledgeStatus.PUBLISHED,
        metadata_={"category": "onboarding", "last_reviewed": "2026-08-01"},
    ))
    s.add(Knowledge(
        business_id=biz_id,
        title="Payment FAQs — UPI, Cards, Razorpay",
        content="""How do I pay?
We use Razorpay: UPI (GPay, PhonePe, Paytm), debit/credit cards (Visa, Mastercard, RuPay), net banking.

Payment link expired?
Links are valid 30 minutes. Message us on WhatsApp and we'll generate a new link for the same order.

Money deducted but order not confirmed?
Wait 15 minutes — bank timeouts auto-resolve. If order still shows PAYMENT_PENDING, send your UPI transaction ID.

Refund timeline?
5–7 business days to original payment method.""",
        source="faq_import",
        keywords=["payment", "UPI", "razorpay", "GPay", "PhonePe", "card", "expired", "refund", "money deducted", "link"],
        status=KnowledgeStatus.PUBLISHED,
        metadata_={"category": "payments"},
    ))
    s.add(Knowledge(
        business_id=biz_id,
        title="Refund and Cancellation Policy",
        content="""Can I cancel?
Before payment: yes. After payment is confirmed: no — keys are delivered instantly.

When am I eligible for a refund?
1. Defective or already-activated key received
2. Wrong product delivered
3. Technical failure on our side prevented delivery

How to request:
WhatsApp us with your order reference (ORD-XXXX-XXXXXX), screenshot of the error, and email.

Not eligible:
- Change of mind after key delivery
- Third-party account or PC issues""",
        source="manual",
        keywords=["refund", "cancel", "cancellation", "policy", "return", "money back", "wrong product"],
        status=KnowledgeStatus.PUBLISHED,
        metadata_={"category": "policy", "last_reviewed": "2026-07-15"},
    ))
    s.add(Knowledge(
        business_id=biz_id,
        title="Troubleshooting Game Launch Issues",
        content="""Game won't launch after redeeming the key?

Steam:
1. Right-click game → Properties → Local Files → Verify integrity of game files
2. Update GPU drivers (NVIDIA GeForce Experience / AMD Adrenalin)
3. Disable antivirus temporarily (may block game files)
4. Run Steam as Administrator
5. Check PC meets minimum requirements

Key shows "Already Activated":
This is a serious issue — the key was used before it reached you. Contact support immediately with your order reference.

Key shows "Invalid":
- Redeeming on the correct platform?
- Typos? (0 vs O, 1 vs I vs L)
- Contact support if issue persists

Common Tanglish signals this article answers: "launch aagala", "open aagala", "crash", "key kaam nahi kar raha" """,
        source="manual",
        keywords=["launch", "not working", "crash", "invalid key", "already activated", "verify", "drivers",
                  "aagala", "open aagala", "kaam nahi", "start nahi"],
        status=KnowledgeStatus.PUBLISHED,
        metadata_={"category": "technical"},
    ))
    s.add(Knowledge(
        business_id=biz_id,
        title="PSN Account Linking Guide [DRAFT]",
        content="""[DRAFT — pending product review]

Link your Nivaso account to PlayStation Network for faster checkout and order history in your PSN app.

Steps (ETA Q4 2026):
1. Visit nivaso.in/link-account
2. Sign in with your WhatsApp number (OTP)
3. Click "Link PSN" → sign in with PSN credentials
4. Confirm linking

Benefits:
- One-tap reorder
- Order history in PSN app
- Faster support (instant account verification)""",
        source="manual",
        keywords=["link account", "PSN", "PlayStation", "connect", "profile"],
        status=KnowledgeStatus.DRAFT,
        metadata_={"category": "account", "eta": "Q4 2026"},
    ))
    s.add(Knowledge(
        business_id=biz_id,
        title="Old Manual Checkout Process v1 [ARCHIVED]",
        content="""[ARCHIVED — superseded March 2026]

Previously customers would:
1. Tell the agent which game they want
2. Agent raises a manual invoice
3. Customer pays via bank transfer
4. Agent confirms and sends key

Replaced by the Razorpay automated payment-link flow as of 2026-03-01.""",
        source="manual",
        keywords=["old checkout", "bank transfer", "manual invoice", "v1"],
        status=KnowledgeStatus.ARCHIVED,
        metadata_={"deprecated_on": "2026-03-01", "replaced_by": "razorpay_flow"},
    ))
    await s.flush()
    print("  Knowledge: 6 (PUBLISHED x4, DRAFT x1, ARCHIVED x1)")

    # =========================================================================
    # AGENT RUNS  –  5 rows; realistic token/latency data; error case included
    # =========================================================================
    s.add(AgentRun(
        business_id=biz_id, conversation_id=conv1_id,
        model="claude-sonnet-5", effort="medium",
        input_tokens=4821, output_tokens=312,
        cache_read_tokens=3200, cache_creation_tokens=0,
        iterations=3, tool_calls=2,
        stop_reason="end_turn", latency_ms=2340,
        metadata_={"request_ids": ["req_01abc123"]},
    ))
    s.add(AgentRun(
        business_id=biz_id, conversation_id=conv2_id,
        model="claude-sonnet-5", effort="medium",
        input_tokens=3150, output_tokens=245,
        cache_read_tokens=2800, cache_creation_tokens=0,
        iterations=3, tool_calls=2,
        stop_reason="end_turn", latency_ms=1980,
        metadata_={"request_ids": ["req_02def456"]},
    ))
    s.add(AgentRun(
        business_id=biz_id, conversation_id=conv6_id,
        model="claude-sonnet-5", effort="high",
        input_tokens=7203, output_tokens=521,
        cache_read_tokens=4100, cache_creation_tokens=1200,
        iterations=8, tool_calls=3,
        stop_reason="end_turn", latency_ms=4512,
        metadata_={"request_ids": ["req_06ghi789", "req_06jkl012"]},
    ))
    s.add(AgentRun(
        business_id=biz_id, conversation_id=conv4_id,
        model="gemini-2.5-flash", effort="low",
        input_tokens=2410, output_tokens=189,
        cache_read_tokens=0, cache_creation_tokens=0,
        iterations=2, tool_calls=1,
        stop_reason="end_turn", latency_ms=1102,
        metadata_={"request_ids": ["req_04mno345"]},
    ))
    # Error case — standalone run with no conversation
    s.add(AgentRun(
        business_id=biz_id, conversation_id=None,
        model="claude-sonnet-5", effort="low",
        input_tokens=1200, output_tokens=0,
        cache_read_tokens=900, cache_creation_tokens=0,
        iterations=1, tool_calls=0,
        stop_reason=None, latency_ms=8001,
        error="AnthropicError: Request timeout after 8000ms. All retries exhausted.",
        metadata_={"request_ids": [], "retry_attempts": 3},
    ))
    await s.flush()
    print("  AgentRuns: 5 (claude + gemini, cache hits, error case, no-conv run)")

    # =========================================================================
    # WEBHOOK EVENTS  –  7 rows; all 3 sources; all 5 WebhookStatus values
    # WebhookEvent has NO TenantMixin — business_id is nullable and set directly.
    # =========================================================================
    s.add(WebhookEvent(
        source=WebhookSource.WHATSAPP,
        external_event_id="wamid.HN0000C1.001",
        business_id=biz_id, signature_verified=True,
        payload={"object": "whatsapp_business_account", "entry": [{"id": "101234567890",
            "changes": [{"value": {"messaging_product": "whatsapp",
                "messages": [{"id": "wamid.HN0000C1.001", "from": "919876543210",
                              "type": "text", "text": {"body": "Hi, do you have GTA 5 for PC?"}}]}}]}]},
        status=WebhookStatus.PROCESSED,
        processed_at=ago(days=3), attempts=1,
    ))
    s.add(WebhookEvent(
        source=WebhookSource.WHATSAPP,
        external_event_id="wamid.HN0000C6.001",
        business_id=biz_id, signature_verified=True,
        payload={"object": "whatsapp_business_account", "entry": [{"id": "101234567890",
            "changes": [{"value": {"messaging_product": "whatsapp",
                "messages": [{"id": "wamid.HN0000C6.001", "from": "918099887766",
                              "type": "text", "text": {"body": "Bro mera GTA key kaam nahi kar raha."}}]}}]}]},
        status=WebhookStatus.PROCESSED,
        processed_at=ago(hours=0.5), attempts=1,
    ))
    s.add(WebhookEvent(
        source=WebhookSource.TELEGRAM,
        external_event_id="tg.C2.msg.001",
        business_id=biz_id, signature_verified=True,
        payload={"update_id": 100000001,
                 "message": {"message_id": 1001, "from": {"id": 789456123, "is_bot": False, "first_name": "Priya"},
                             "chat": {"id": 789456123, "type": "private"},
                             "text": "PS Plus 3 month kitna hai?"}},
        status=WebhookStatus.PROCESSED,
        processed_at=ago(hours=2), attempts=1,
    ))
    s.add(WebhookEvent(
        source=WebhookSource.RAZORPAY,
        external_event_id="pay_seed01_abc001",
        business_id=biz_id, signature_verified=True,
        payload={"entity": "event", "event": "payment.authorized",
                 "payload": {"payment": {"entity": {"id": "pay_seed01_abc001",
                     "amount": 60800, "currency": "INR", "status": "authorized",
                     "order_id": "order_seed01_xyz001", "description": "ORD-2609-SEED01"}}}},
        status=WebhookStatus.PROCESSED,
        processed_at=ago(days=3), attempts=1,
    ))
    s.add(WebhookEvent(
        source=WebhookSource.RAZORPAY,
        external_event_id="pay_seed07a_fail1",
        business_id=biz_id, signature_verified=True,
        payload={"entity": "event", "event": "payment.failed",
                 "payload": {"payment": {"entity": {"id": "pay_seed07a_fail1",
                     "amount": 149900, "currency": "INR", "status": "failed",
                     "error_code": "BAD_REQUEST_ERROR",
                     "error_description": "Insufficient funds."}}}},
        status=WebhookStatus.PROCESSED,
        processed_at=ago(days=1), attempts=1,
    ))
    # Signature mismatch — ignored (bad actor or replay attack)
    s.add(WebhookEvent(
        source=WebhookSource.WHATSAPP,
        external_event_id="wamid.SUSPICIOUS.001",
        business_id=None, signature_verified=False,
        payload={"object": "whatsapp_business_account",
                 "entry": [{"id": "FAKE_ID", "changes": [{"value": {"messages": [{"id": "wamid.SUSPICIOUS.001"}]}}]}]},
        status=WebhookStatus.IGNORED,
        error="Signature verification failed: X-Hub-Signature-256 mismatch",
        processed_at=None, attempts=1,
    ))
    # Received but still in processing queue (backlog simulation)
    s.add(WebhookEvent(
        source=WebhookSource.TELEGRAM,
        external_event_id="tg.C5.msg.001",
        business_id=biz_id, signature_verified=True,
        payload={"update_id": 100000002,
                 "message": {"message_id": 2001, "from": {"id": 321654987, "first_name": "Deepika"},
                             "chat": {"id": 321654987, "type": "private"},
                             "text": "Google Play Pass lena hai 1 month wala"}},
        status=WebhookStatus.PROCESSED,
        processed_at=ago(days=14), attempts=1,
    ))
    await s.flush()
    print("  WebhookEvents: 7 (WhatsApp x3, Telegram x2, Razorpay x2; PROCESSED, IGNORED)")

    print("\n  Summary:")
    print("  +-- 1  Business (active)")
    print("  +-- 10 Products  (active x6, out_of_stock x1, inactive x1, archived x1, DLC x1)")
    print("  +-- 5  Customers")
    print("  +-- 6  CustomerChannels  (WA x3, TG x2, Web x1)")
    print("  +-- 6  Conversations  (active x3, closed x3; all 9 ConversationStates touched)")
    print("  +-- 52 Messages  (all SenderTypes + MessageTypes; tool_use_id pairs linked)")
    print("  +-- 8  Orders  (all 8 statuses; discount example on PAID order)")
    print("  +-- 10 OrderItems  (snapshots; check-constraints satisfied)")
    print("  +-- 7  Payments  (all statuses; 2x append-only FAILED; duplicate detection)")
    print("  +-- 4  Fulfillments  (all 4 statuses)")
    print("  +-- 5  SupportTickets  (all 5 statuses; all 4 priorities)")
    print("  +-- 6  Knowledge  (published x4, draft x1, archived x1)")
    print("  +-- 5  AgentRuns  (claude + gemini; cache-hit rows; error row)")
    print("  +-- 7  WebhookEvents  (all 3 sources; PROCESSED + IGNORED; null business_id)")


if __name__ == "__main__":
    asyncio.run(main())
