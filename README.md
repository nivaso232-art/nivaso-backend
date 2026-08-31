# Nivaso Backend

AI-powered sales and customer support platform for businesses that sell over **WhatsApp** and **Telegram**. Customers chat naturally in their own language (including Tanglish), and a Claude AI agent handles product discovery, order placement, payment, and support — automatically.

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Tech Stack](#tech-stack)
3. [Project Structure](#project-structure)
4. [Complete Workflow](#complete-workflow)
   - [Customer Sends a Message](#1-customer-sends-a-message)
   - [Webhook Verification and Logging](#2-webhook-verification-and-logging)
   - [Customer and Conversation Resolution](#3-customer-and-conversation-resolution)
   - [Agent Turn — AI Reads and Replies](#4-agent-turn--ai-reads-and-replies)
   - [Tool Execution — How the AI Acts](#5-tool-execution--how-the-ai-acts)
   - [Payment Flow](#6-payment-flow)
   - [Payment Confirmation via Razorpay Webhook](#7-payment-confirmation-via-razorpay-webhook)
   - [Fulfillment](#8-fulfillment)
   - [Support Escalation](#9-support-escalation)
   - [Reply Sent Back to Customer](#10-reply-sent-back-to-customer)
5. [Module Interactions](#module-interactions)
6. [API Reference](#api-reference)
7. [Database Overview](#database-overview)
8. [Setup and Running](#setup-and-running)
9. [Environment Variables](#environment-variables)
10. [Key Design Rules](#key-design-rules)

---

## What It Does

```
Customer (WhatsApp / Telegram)
        │  "bro GTA 5 irukka? price?"
        ▼
    Nivaso AI Agent
        │  searches catalog → quotes ₹229
        │  customer confirms → creates order
        │  sends Razorpay payment link
        │  Razorpay webhook confirms payment
        │  AI tells customer "order confirmed!"
        ▼
Customer gets their product
```

If the AI cannot resolve something (refund request, product access problem, complaint) it escalates to a human agent by creating a support ticket and telling the customer a team member will follow up.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI + Uvicorn |
| Database | PostgreSQL (via Supabase) |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Migrations | Alembic |
| AI | Anthropic SDK — Claude (configurable model) |
| Payments | Razorpay Payment Links API |
| Messaging (receive) | WhatsApp Cloud API (Meta) + Telegram Bot API |
| Messaging (send) | WhatsApp Cloud API + Telegram Bot API |
| Media storage | Supabase Storage |
| HTTP client | httpx (async) |
| Retries | tenacity |
| Logging | structlog (JSON in prod, colored console in dev) |
| Validation | Pydantic v2 + pydantic-settings |

---

## Project Structure

```
nivaso-backend/
│
├── app/
│   ├── main.py               # FastAPI app — mounts all routers, lifespan
│   │
│   ├── core/                 # Shared infrastructure
│   │   ├── config.py         # All env vars in one place (settings object)
│   │   ├── db.py             # Async SQLAlchemy engine + session factory
│   │   ├── uow.py            # Unit of Work — explicit transaction boundary
│   │   ├── errors.py         # Custom exceptions + FastAPI error handlers
│   │   ├── security.py       # HMAC-SHA256 webhook signature verification
│   │   ├── logging.py        # structlog configuration
│   │   ├── ids.py            # Human-readable reference generator (ORD-2608-7F3K9Q)
│   │   └── supabase.py       # Media upload/signed-URL client
│   │
│   ├── models/               # SQLAlchemy ORM table definitions (14 tables)
│   │   ├── enums.py          # All Postgres enum types
│   │   ├── business.py       # businesses
│   │   ├── product.py        # products
│   │   ├── customer.py       # customers + customer_channels
│   │   ├── conversation.py   # conversations + messages
│   │   ├── order.py          # orders + order_items
│   │   ├── payment.py        # payments
│   │   ├── fulfillment.py    # fulfillments
│   │   ├── support_ticket.py # support_tickets
│   │   ├── knowledge.py      # knowledge
│   │   ├── agent_run.py      # agent_runs
│   │   └── webhook_event.py  # webhook_events
│   │
│   ├── repositories/         # SQL query layer (tenant-scoped)
│   │   ├── base.py           # BaseRepository + GlobalRepository
│   │   ├── businesses.py     # lookup by slug
│   │   ├── products.py       # full-text + trigram search
│   │   ├── customers.py      # resolve by channel handle
│   │   ├── conversations.py  # active conversation, message history
│   │   ├── orders.py         # by reference, latest open
│   │   ├── payments.py       # cross-tenant lookup by provider ID
│   │   ├── fulfillments.py   # delivery status
│   │   ├── support_tickets.py# open tickets, by reference
│   │   ├── knowledge.py      # full-text + trigram search
│   │   ├── agent_runs.py     # token totals per tenant
│   │   └── webhook_events.py # idempotency insert + status updates
│   │
│   ├── services/             # Business logic layer
│   │   ├── state_machine.py  # validates status transitions
│   │   ├── order_service.py  # create/cancel orders, price from DB
│   │   ├── payment_service.py# apply provider outcomes, detect duplicates
│   │   ├── customer_service.py# resolve/create customer + channel
│   │   ├── conversation_service.py # message log, conversation lifecycle
│   │   ├── catalog_service.py# product search, authoritative price
│   │   ├── knowledge_service.py # help article search
│   │   ├── support_service.py# create/assign/resolve tickets
│   │   └── fulfillment_service.py # delivery status
│   │
│   ├── agent/                # AI agent layer
│   │   ├── prompts.py        # cached system prompt + volatile turn context
│   │   ├── context.py        # ToolContext — tenant/customer injected server-side
│   │   ├── registry.py       # fixed ordered list of 9 tools
│   │   ├── runner.py         # Anthropic tool-call loop + AgentRun recording
│   │   └── tools/
│   │       ├── catalog.py    # search_products, get_product
│   │       ├── orders.py     # create_order, get_order_status, cancel_order
│   │       ├── payments.py   # create_payment_link, check_payment_status
│   │       ├── knowledge.py  # search_knowledge
│   │       └── support.py    # create_support_ticket
│   │
│   ├── providers/            # External API clients (send-only)
│   │   ├── razorpay/client.py  # create_payment_link + parse_webhook_outcome
│   │   ├── whatsapp/client.py  # send_text via Meta Graph API
│   │   └── telegram/client.py  # send_message via Telegram Bot API
│   │
│   ├── channels/             # Inbound message parsers (receive-only)
│   │   ├── whatsapp/parser.py  # parse Meta batch envelope → InboundMessage list
│   │   └── telegram/parser.py  # parse Telegram Update → InboundMessage
│   │
│   └── api/                  # FastAPI route handlers
│       ├── deps.py           # shared dependencies (session, auth, business lookup)
│       ├── webhooks/
│       │   ├── whatsapp.py   # GET (verify) + POST (inbound messages)
│       │   ├── telegram.py   # POST (inbound updates)
│       │   └── razorpay.py   # POST (payment events)
│       └── admin/            # Internal API (X-Internal-Key protected)
│           ├── businesses.py # CRUD businesses
│           ├── products.py   # CRUD products
│           ├── support.py    # list/update support tickets
│           ├── customers.py  # list/view customers
│           └── knowledge.py  # CRUD knowledge articles
│
├── migrations/
│   └── versions/
│       ├── 0001_initial_schema.py   # all 14 tables + 16 enum types
│       └── 0002_search_rls_indexes.py # GIN + trigram indexes for search
│
├── .env.example              # env var template
├── alembic.ini               # migration config
├── requirements.txt          # production dependencies
├── requirements-dev.txt      # dev tools (pytest, ruff, mypy)
├── STRUCTURE.md              # folder + file reference
└── SCHEMA.md                 # database schema reference
```

---

## Complete Workflow

### 1. Customer Sends a Message

A customer opens WhatsApp or Telegram and sends a message to the business number/bot.

**WhatsApp path:**
- Meta sends a `POST` to `/webhooks/whatsapp`
- The request body contains a batch envelope — potentially multiple messages from multiple senders

**Telegram path:**
- Telegram sends a `POST` to `/webhooks/telegram`
- One Update object per request

```
Customer → WhatsApp/Telegram → Meta/Telegram servers → POST /webhooks/*
```

---

### 2. Webhook Verification and Logging

Before anything else, two things happen synchronously:

**Step 1 — Signature verification**
Every inbound webhook is HMAC-SHA256 verified against the provider's secret before the body is read further. An invalid signature returns `401` immediately and nothing is processed.

| Provider | Header | Secret |
|----------|--------|--------|
| WhatsApp | `X-Hub-Signature-256: sha256=<hex>` | `WHATSAPP_APP_SECRET` |
| Telegram | `X-Telegram-Bot-Api-Secret-Token: <token>` | `TELEGRAM_WEBHOOK_SECRET` |
| Razorpay | `X-Razorpay-Signature: <hex>` | `RAZORPAY_WEBHOOK_SECRET` |

**Step 2 — Return 200 immediately**
After signature verification, the handler returns `200 OK` at once and hands off processing to a `BackgroundTask`. This is critical: Meta and Razorpay retry delivery on any non-2xx response, and a 10-second AI turn must not trigger a duplicate.

**Step 3 — Idempotency check**
The background task writes a row to `webhook_events` using `ON CONFLICT DO NOTHING`. If this event was already processed (provider retry), the row already exists → the function returns early. Only the first delivery proceeds.

```python
# webhook_events table
source            = "whatsapp"
external_event_id = "wamid.abc123"   # synthesised from message id
payload           = { ...raw body... }
status            = RECEIVED → PROCESSING → PROCESSED / FAILED
```

**Modules involved:**
- `api/webhooks/whatsapp.py` — route handler
- `core/security.py` — HMAC verification
- `repositories/webhook_events.py` — idempotency insert

---

### 3. Customer and Conversation Resolution

Once the webhook is confirmed as new, the payload is parsed and the system figures out who sent the message and whether they have an active conversation.

**Parse the message**

```
channels/whatsapp/parser.py → parse_webhook(payload) → [InboundMessage]
channels/telegram/parser.py → parse_update(payload)  → InboundMessage
```

Each `InboundMessage` contains:
- `wa_id` / `chat_id` — the sender's platform ID
- `display_name` — name from the platform profile
- `text` — message body (None for non-text)
- `external_message_id` — the platform's own message ID

**Resolve the business**

```python
BusinessRepository.get_active_or_raise(settings.default_business_slug)
```

A suspended business stops here — its agent will not respond.

**Resolve or create the customer**

```python
CustomerService.resolve_or_create(
    channel=Channel.WHATSAPP,
    external_user_id="919876543210",
    display_name="Rajesh Kumar",
)
```

Logic:
1. Look up `customer_channels` by `(business_id, channel, external_user_id)`
2. If found → returning customer, return as-is
3. If not found but phone matches an existing customer → link this channel to that customer (same person on WhatsApp + Telegram = one customer, two channels)
4. If not found at all → create new `Customer` + `CustomerChannel`

**Get or create the active conversation**

```python
ConversationService.get_or_create_active(
    customer_id=customer.id,
    customer_channel_id=channel_row.id,
    channel=Channel.WHATSAPP,
)
```

A partial unique index (`uq_conversations_active_per_channel`) ensures that even if two webhooks arrive simultaneously for the same customer, only one conversation is created.

**Record the inbound message**

```python
ConversationService.record_inbound(
    conversation=conversation,
    content="bro GTA 5 irukka?",
    external_message_id="wamid.abc123",
)
```

If `external_message_id` already exists in `messages` → duplicate delivery → stop here, return early.

**Modules involved:**
- `channels/whatsapp/parser.py`, `channels/telegram/parser.py`
- `repositories/businesses.py`
- `services/customer_service.py`
- `services/conversation_service.py`
- `repositories/customers.py`, `repositories/conversations.py`

---

### 4. Agent Turn — AI Reads and Replies

With customer, conversation, and inbound message established, the AI turn begins.

**Build context for the prompt**

```python
# Stable half — cached between turns for this business
categories      = await CatalogService.list_categories()
knowledge_titles = await KnowledgeService.index_summary()

# Volatile half — injected per turn, never cached
turn_context = build_turn_context(customer, conversation, open_order)
```

**Load conversation history**

```python
history = await ConversationService.history(conversation, limit=40)
```

The last 40 messages (including tool calls and results) are loaded and converted to the Anthropic Messages API format. Tool traffic is reconstructed as `assistant:[tool_use]` + `user:[tool_result]` pairs.

**Call the Anthropic API**

```python
response = await anthropic_client.messages.create(
    model=settings.agent_model,          # e.g. claude-opus-5
    max_tokens=settings.agent_max_tokens,
    system=cached_system_prompt,         # business rules + tool guidance
    tools=api_tools(),                   # 9 tool definitions
    messages=history + [user_message],
)
```

The system prompt is split so the stable part (business rules, tool guidance, product categories) carries a `cache_control: ephemeral` breakpoint and is served from Anthropic's prompt cache on repeat turns — significantly reducing input token cost.

**Modules involved:**
- `agent/runner.py` — orchestrates the loop
- `agent/prompts.py` — builds system prompt
- `services/catalog_service.py`, `services/knowledge_service.py`
- `repositories/conversations.py` — loads history

---

### 5. Tool Execution — How the AI Acts

The AI does not have free-form write access to the database. It can only act through 9 predefined tools. Each tool call goes through `agent/runner.py` which:

1. Records the tool call to `messages` (audit trail — Rule 5)
2. Executes the tool handler with `ToolContext`
3. Records the tool result to `messages`
4. Sends the result back to the API
5. Loops until `end_turn` or `max_iterations`

**The 9 tools and what they do:**

| Tool | Action | Key restriction |
|------|--------|----------------|
| `search_products` | Full-text search the catalog | Read-only |
| `get_product` | Fetch one product with current price | Read-only |
| `create_order` | Place an order | Accepts only `product_id + quantity` — no price parameter |
| `get_order_status` | Check order and fulfillment status | Read-only |
| `cancel_order` | Cancel an unpaid order | Blocked on PAID orders |
| `create_payment_link` | Generate a Razorpay link | Amount read from order, not model input |
| `check_payment_status` | Read verified payment records | Read-only, reports only signed-webhook data |
| `search_knowledge` | Search help articles | Read-only |
| `create_support_ticket` | Escalate to a human agent | Creates a ticket + sets conversation to HUMAN_HANDOFF |

**ToolContext — tenant isolation**

Every tool handler receives a `ToolContext` object that carries `business_id`, `customer_id`, and `conversation_id`. These are set server-side from the verified webhook — they are not parameters in any tool schema. The model has no mechanism to address a different tenant, customer, or conversation.

**create_order flow (example):**

```
AI calls create_order({ items: [{product_id: "uuid", quantity: 1}] })
    │
    ▼
OrderService.create_order(customer_id, lines)
    │  1. Fetch products from DB (tenant-scoped)
    │  2. Read price from product.price — the only source
    │  3. Compute subtotal, apply discount
    │  4. INSERT order + order_items in one transaction
    ▼
Returns: {order_reference, total, items, status}
    │
    ▼
AI reads total aloud to customer — price came from DB, not the model
```

**Modules involved:**
- `agent/runner.py` — loop, recording
- `agent/registry.py` — tool lookup
- `agent/context.py` — ToolContext
- `agent/tools/*.py` — tool handlers
- `services/order_service.py`, `services/payment_service.py`, etc.

---

### 6. Payment Flow

Once the customer confirms the order total:

```
Customer: "ok send the link"
    │
    ▼
AI calls create_payment_link({ order_reference: "ORD-2608-7F3K9Q" })
    │
    ▼
providers/razorpay/client.py → RazorpayClient.create_payment_link()
    │  POST https://api.razorpay.com/v1/payment_links
    │  amount = order.total_in_minor_units()  (paise)
    │  reference_id = order.reference
    ▼
Returns: PaymentLinkResult(link_id="plink_xxx", short_url="https://rzp.io/...")
    │
    ▼
PaymentService.create_attempt()
    │  INSERT payments row (status=PENDING, provider_payment_link_id="plink_xxx")
    │  payment_url = short_url
    ▼
OrderService.mark_payment_pending()
    │  order.status → PAYMENT_PENDING
    ▼
AI sends the short_url to the customer
```

The AI is explicitly instructed: **do not tell the customer payment succeeded until `check_payment_status` returns `is_paid = true`**. A customer claiming "I paid" does not change the order status.

**Modules involved:**
- `agent/tools/payments.py` — create_payment_link tool
- `providers/razorpay/client.py` — Razorpay API call
- `services/payment_service.py` — creates PENDING attempt
- `services/order_service.py` — marks PAYMENT_PENDING

---

### 7. Payment Confirmation via Razorpay Webhook

When the customer pays, Razorpay sends a webhook to `/webhooks/razorpay`.

```
Razorpay → POST /webhooks/razorpay
    │
    ├── Verify HMAC-SHA256 (X-Razorpay-Signature)
    ├── Return 200 immediately
    └── Background task:
        │
        ├── Write webhook_events row (idempotency)
        │
        ├── parse_webhook_outcome(payload)
        │   → ProviderOutcome(provider_payment_id, status=SUCCESS, amount)
        │
        ├── Global payment lookup (cross-tenant)
        │   PaymentRepository.get_by_provider_link_id("plink_xxx")
        │   → finds Payment row, reads payment.business_id
        │
        ├── PaymentService.apply_provider_outcome(outcome)
        │   ├── Locate payment by provider_payment_id or link_id
        │   ├── Check for duplicate (Rule 7):
        │   │   - If order already PAID → mark is_duplicate=True, needs_refund=True
        │   │   - Auto-create support ticket for refund
        │   ├── payment.status → SUCCESS
        │   └── order.status → PAID
        │
        └── Mark webhook_event PROCESSED
```

This is the **only place in the entire codebase** where an order transitions to `PAID`. No agent tool, admin API, or service method can mark an order paid directly.

**Double-charge protection (Rule 7):**
If a customer somehow pays twice, the second SUCCESS is recorded truthfully (the money did move) but flagged `is_duplicate = true` and `needs_refund = true`. A support ticket is automatically created. The order status is not changed again.

**Modules involved:**
- `api/webhooks/razorpay.py` — route handler
- `core/security.py` — signature verification
- `providers/razorpay/client.py` — `parse_webhook_outcome()`
- `repositories/payments.py` — global lookup
- `services/payment_service.py` — `apply_provider_outcome()`
- `services/support_service.py` — auto-escalation on anomalies

---

### 8. Fulfillment

After an order is `PAID`, a human (or an automated script) delivers the product and records it:

```
Admin PATCH /admin/{slug}/support/{ref}  ← human marks ticket resolved
            or
Custom fulfillment script
    │
    ▼
FulfillmentService.create_fulfillment(order, credential_ref="vault/key/xyz")
    │  INSERT fulfillments row (status=PENDING)
    │  credential_ref = pointer to secret, never the secret itself
    ▼
fulfillment.status → READY → DELIVERED
```

The customer can ask the AI at any time:

```
Customer: "bro game kuduthaanga?"
    │
    ▼
AI calls get_order_status({ order_reference: "ORD-2608-7F3K9Q" })
    │
    ▼
FulfillmentService.status_for_order(order.id)
    → fulfillment_status = "DELIVERED"
    │
    ▼
AI: "Yes, your game was delivered! Check your email for the access key."
```

**Modules involved:**
- `services/fulfillment_service.py`
- `repositories/fulfillments.py`
- `agent/tools/orders.py` — `get_order_status` reads fulfillment status

---

### 9. Support Escalation

When the AI cannot resolve something, it creates a support ticket:

```
Customer: "bro refund venum" (I want a refund)
    │
    ▼
AI calls create_support_ticket({
    reason: "REFUND_REQUEST",
    summary: "Customer paid for GTA 5 (ORD-2608-7F3K9Q) but wants a refund. "
             "Reason: received wrong edition. Needs human review.",
    priority: "HIGH",
    order_reference: "ORD-2608-7F3K9Q"
})
    │
    ▼
SupportService.create_ticket()
    │  Checks: does this conversation already have an open ticket?
    │  If yes → append note + raise priority (no duplicate)
    │  If no  → INSERT support_tickets row (reference = TKT-2608-4M2XQ8)
    ▼
ConversationService.set_state(conversation, HUMAN_HANDOFF)
    │
    ▼
AI: "I've raised a support ticket (TKT-2608-4M2XQ8). A team member will
     follow up with you. They'll sort out the refund."
```

Human agents see the ticket via:
```
GET /admin/{slug}/support                    → list all open tickets
GET /admin/{slug}/support/TKT-2608-4M2XQ8   → ticket detail
PATCH /admin/{slug}/support/TKT-2608-4M2XQ8 → assign, resolve
```

**Modules involved:**
- `agent/tools/support.py` — `create_support_ticket` tool
- `services/support_service.py` — ticket creation logic
- `api/admin/support.py` — human agent routes

---

### 10. Reply Sent Back to Customer

After the AI turn completes, the runner returns the final text reply. The webhook handler sends it back via the appropriate channel client:

**WhatsApp:**
```python
WhatsAppClient.send_text(to="919876543210", text=reply)
→ POST graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages
→ returns wamid (sent message ID)
```

**Telegram:**
```python
TelegramClient.send_message(chat_id="78456321", text=reply)
→ POST api.telegram.org/bot{TOKEN}/sendMessage
→ returns message_id
```

Both clients retry up to 3 times on transient server errors (5xx) using exponential backoff via tenacity.

The assistant reply is written to `messages` with `status=PENDING` before the send attempt — so a failed delivery still has a record of what the AI intended to say.

After sending, the message status is updated to `SENT`.

**Modules involved:**
- `providers/whatsapp/client.py`, `providers/telegram/client.py`
- `services/conversation_service.py` — `record_assistant_reply`, `mark_delivery`

---

## Module Interactions

```
Inbound webhook
    │
    ├── api/webhooks/*.py          Signature verify → 200 → background task
    │       │
    │       ├── repositories/webhook_events.py     Idempotency insert
    │       │
    │       ├── channels/*/parser.py               Parse payload
    │       │
    │       ├── services/customer_service.py        Resolve customer
    │       │   └── repositories/customers.py
    │       │
    │       ├── services/conversation_service.py    Get/create conversation
    │       │   └── repositories/conversations.py
    │       │
    │       └── agent/runner.py                    AI turn
    │               │
    │               ├── agent/prompts.py            Build system prompt
    │               │
    │               ├── services/catalog_service.py  Categories for prompt
    │               ├── services/knowledge_service.py Titles for prompt
    │               │
    │               ├── Anthropic API ─────────────► Claude model
    │               │       │
    │               │       ▼ tool_use blocks
    │               │
    │               ├── agent/registry.py           Look up tool spec
    │               │
    │               ├── agent/tools/*.py            Execute tool handler
    │               │       │
    │               │       ├── services/order_service.py
    │               │       ├── services/payment_service.py
    │               │       ├── services/catalog_service.py
    │               │       ├── services/knowledge_service.py
    │               │       └── services/support_service.py
    │               │               │
    │               │               └── repositories/*.py
    │               │
    │               └── repositories/agent_runs.py  Record turn metrics
    │
    └── providers/*/client.py      Send reply to customer


Razorpay payment webhook
    │
    ├── api/webhooks/razorpay.py   Verify → 200 → background
    │       │
    │       ├── providers/razorpay/client.py        parse_webhook_outcome()
    │       │
    │       ├── repositories/payments.py            Global payment lookup
    │       │
    │       ├── services/payment_service.py         apply_provider_outcome()
    │       │   └── Order status → PAID
    │       │
    │       └── services/support_service.py         Auto-ticket on anomalies


Admin API
    │
    ├── api/admin/*.py             X-Internal-Key verified
    │       │
    │       ├── repositories/*.py                   CRUD operations
    │       └── services/*.py                       Business logic
```

---

## API Reference

### Webhook Endpoints (Public)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/webhooks/whatsapp` | Meta verification handshake |
| `POST` | `/webhooks/whatsapp` | Inbound WhatsApp messages |
| `POST` | `/webhooks/telegram` | Inbound Telegram updates |
| `POST` | `/webhooks/razorpay` | Razorpay payment events |

### Admin Endpoints (Require `X-Internal-Key` header)

**Businesses**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/businesses` | List all businesses |
| `POST` | `/admin/businesses` | Create a business |
| `GET` | `/admin/businesses/{slug}` | Get a business |
| `PATCH` | `/admin/businesses/{slug}` | Update a business |

**Products**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/products` | List products |
| `POST` | `/admin/{slug}/products` | Create a product |
| `GET` | `/admin/{slug}/products/{id}` | Get a product |
| `PATCH` | `/admin/{slug}/products/{id}` | Update a product |
| `DELETE` | `/admin/{slug}/products/{id}` | Archive a product |

**Support Tickets**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/support` | List open tickets |
| `GET` | `/admin/{slug}/support/{ref}` | Get a ticket |
| `PATCH` | `/admin/{slug}/support/{ref}` | Assign / resolve a ticket |

**Customers**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/customers` | List customers |
| `GET` | `/admin/{slug}/customers/{id}` | Get a customer |

**Knowledge Base**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/knowledge` | List articles |
| `POST` | `/admin/{slug}/knowledge` | Create an article |
| `GET` | `/admin/{slug}/knowledge/{id}` | Get an article |
| `PATCH` | `/admin/{slug}/knowledge/{id}` | Update an article |
| `DELETE` | `/admin/{slug}/knowledge/{id}` | Archive an article |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}` |

---

## Database Overview

14 tables. Full details in [SCHEMA.md](SCHEMA.md).

```
businesses → products
           → customers → customer_channels → conversations → messages
           → knowledge                    → orders → order_items
                                                   → payments
                                                   → fulfillments
                                          → support_tickets
agent_runs → conversations
webhook_events (global, no tenant until parsed)
```

---

## Setup and Running

### Prerequisites

- Python 3.11+
- PostgreSQL (via Supabase or any Postgres instance)
- A Razorpay account (for payments)
- A Meta developer app (for WhatsApp)
- A Telegram bot token (for Telegram)
- An Anthropic API key

### 1. Clone and install

```bash
git clone https://github.com/nivaso232-art/nivaso-backend.git
cd nivaso-backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in all values in .env
```

### 3. Run database migrations

```bash
alembic upgrade head
```

### 4. Create your first business

```bash
curl -X POST http://localhost:8000/admin/businesses \
  -H "X-Internal-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"slug": "my-business", "name": "My Gaming Store"}'
```

Set `DEFAULT_BUSINESS_SLUG=my-business` in your `.env`.

### 5. Add products

```bash
curl -X POST http://localhost:8000/admin/my-business/products \
  -H "X-Internal-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"name": "GTA 5", "price": 229.00, "currency": "INR", "category": "Game"}'
```

### 6. Register webhooks with providers

**WhatsApp** — set the callback URL in Meta App Dashboard:
```
https://your-domain.com/webhooks/whatsapp
```

**Telegram** — register via Bot API:
```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://your-domain.com/webhooks/telegram" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

### 7. Start the server

```bash
# Development
uvicorn app.main:app --reload

# Production
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Interactive API docs available at `http://localhost:8000/docs` (local env only).

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `APP_ENV` | No | `local` / `staging` / `production` (default: `local`) |
| `LOG_LEVEL` | No | `INFO` / `DEBUG` / `WARNING` (default: `INFO`) |
| `INTERNAL_API_KEY` | Yes | Secret for `X-Internal-Key` header on admin routes |
| `DATABASE_URL` | Yes | Async Postgres URL for the app (Supavisor pooler, port 6543) |
| `DATABASE_DIRECT_URL` | Yes | Direct Postgres URL for Alembic migrations (port 5432) |
| `DB_ECHO` | No | `true` to log all SQL queries |
| `SUPABASE_URL` | No | Supabase project URL (only needed for media storage) |
| `SUPABASE_SERVICE_ROLE_KEY` | No | Supabase service role key (only for media storage) |
| `SUPABASE_MEDIA_BUCKET` | No | Storage bucket name (default: `media`) |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `AGENT_MODEL` | No | Claude model ID (default: `claude-opus-5`) |
| `AGENT_EFFORT` | No | `low` / `medium` / `high` / `xhigh` / `max` (default: `low`) |
| `AGENT_MAX_TOKENS` | No | Max tokens per agent turn (default: `16000`) |
| `AGENT_MAX_ITERATIONS` | No | Max tool-call loop iterations (default: `8`) |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes* | Meta phone number ID |
| `WHATSAPP_ACCESS_TOKEN` | Yes* | Meta Graph API access token |
| `WHATSAPP_VERIFY_TOKEN` | Yes* | Token echoed during Meta webhook verification |
| `WHATSAPP_APP_SECRET` | Yes* | Used to verify `X-Hub-Signature-256` |
| `WHATSAPP_GRAPH_API_VERSION` | No | Graph API version (default: `v21.0`) |
| `TELEGRAM_BOT_TOKEN` | Yes* | Telegram bot token |
| `TELEGRAM_WEBHOOK_SECRET` | Yes* | Secret sent in `X-Telegram-Bot-Api-Secret-Token` |
| `RAZORPAY_KEY_ID` | Yes* | Razorpay API key ID |
| `RAZORPAY_KEY_SECRET` | Yes* | Razorpay API key secret |
| `RAZORPAY_WEBHOOK_SECRET` | Yes* | Used to verify `X-Razorpay-Signature` |
| `DEFAULT_BUSINESS_SLUG` | Yes | Slug of the business inbound webhooks route to |

*Required only if using that channel/provider.

---

## Key Design Rules

These rules are enforced structurally — the code makes them impossible to violate, not just unlikely.

| Rule | What it means | How it's enforced |
|------|--------------|-------------------|
| **1. AI never sets a price** | Orders are priced from the DB, not from the model | `create_order` has no price parameter; prices are fetched inside the service |
| **2. Only signed webhooks confirm payment** | A customer saying "I paid" changes nothing | `apply_provider_outcome` is only called from the Razorpay webhook handler |
| **3. AI never marks fulfillment** | The agent has no tool to mark anything delivered | No such tool exists in the registry |
| **4. Tenant isolation** | The model cannot address another business's data | `business_id`, `customer_id`, `conversation_id` come from `ToolContext`, not model input |
| **5. Full audit trail** | Every tool call and result is persisted | `record_tool_call` + `record_tool_result` wrap every tool execution in the runner |
| **6. Append-only payments** | Failed attempts are never mutated to succeeded | A retry creates a new payments row; old rows are never updated to SUCCESS |
| **7. Double-charge detection** | A second payment on an already-paid order is flagged, not swallowed | `_apply_success` checks for an existing SUCCESS and sets `is_duplicate + needs_refund` |
| **8. Conversation state is separate from order state** | Browsing mid-checkout doesn't cancel the order | `current_state` on conversations is independent of `status` on orders |
| **9. Idempotent webhooks** | Provider retries are no-ops | `ON CONFLICT DO NOTHING` on `webhook_events`; unique index on `messages.external_message_id` |
