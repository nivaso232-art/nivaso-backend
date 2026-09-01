# Nivaso Backend — Folder & File Structure

## Root

```
nivaso-backend/
├── app/                    # All application source code
├── migrations/             # Alembic database migration scripts
├── .env.example            # Template for environment variables (copy → .env)
├── .gitignore              # Git ignore rules
├── alembic.ini             # Alembic config (migration script location, DB URL)
├── requirements.txt        # Production Python dependencies
├── requirements-dev.txt    # Dev-only tools (pytest, ruff, mypy)
└── STRUCTURE.md            # This file
```

---

## `app/` — Application Source

```
app/
├── main.py                 # FastAPI app entry point. Mounts all routers, registers
│                           # exception handlers, runs startup/shutdown lifecycle.
│
├── core/                   # Shared infrastructure (config, DB, logging, etc.)
├── models/                 # SQLAlchemy ORM table definitions
├── repositories/           # Database query layer (tenant-scoped reads/writes)
├── services/               # Business logic (rules, state machines, calculations)
├── agent/                  # AI agent — prompts, tools, runner
├── providers/              # External API clients (Razorpay, WhatsApp, Telegram)
├── channels/               # Inbound message parsers per messaging platform
└── api/                    # FastAPI route handlers (webhooks + admin)
```

---

## `app/core/` — Infrastructure Layer

Every module here is a building block used by the rest of the app.

| File | Purpose |
|------|---------|
| `config.py` | Single source of truth for all env vars. Import `settings` from here — never use `os.getenv` elsewhere. |
| `db.py` | Creates the async SQLAlchemy engine and session factory. Provides `get_db` dependency for FastAPI. |
| `uow.py` | **Unit of Work** — wraps a session in a single database transaction. Any code that writes more than one row uses `async with UnitOfWork(session)`. |
| `errors.py` | Custom exception classes (`NotFoundError`, `ConflictError`, `SignatureError`, etc.) and the FastAPI exception handlers that turn them into JSON responses. |
| `security.py` | HMAC-SHA256 signature verification for Meta, Razorpay, and Telegram webhooks. Also validates the internal admin API key. |
| `logging.py` | Configures structlog. JSON output in staging/prod, colored console in local. Call `bind_request_context()` at the start of each request to attach IDs to all log lines. |
| `ids.py` | Generates human-readable order/ticket references like `ORD-2608-7F3K9Q`. Also has `normalize_reference()` to fix typos customers make. |
| `supabase.py` | Supabase Storage client — used only for saving media files (images, audio) that arrive from WhatsApp/Telegram before their URLs expire. |

---

## `app/models/` — Database Tables (ORM)

Each file defines one or more SQLAlchemy model classes that map directly to Postgres tables.

| File | Tables | What it represents |
|------|--------|--------------------|
| `enums.py` | *(no table)* | All Python + Postgres enum types in one place (e.g. `OrderStatus`, `PaymentStatus`, `Channel`). |
| `base.py` | *(no table)* | Shared mixins used by every model: `UUIDMixin` (uuid primary key), `TenantMixin` (business_id), `TimestampMixin` (created_at, updated_at). |
| `business.py` | `businesses` | The **tenant root**. Every other record belongs to a business. Has a `slug` (URL-safe name) and per-tenant `settings` JSON. |
| `product.py` | `products` | The catalog. Stores price, category, status, and a Postgres-generated full-text search document (`search_doc`). |
| `customer.py` | `customers`, `customer_channels` | `customers` is the person. `customer_channels` is how they reach you (their WhatsApp ID, Telegram chat ID). One person can have both. |
| `conversation.py` | `conversations`, `messages` | A `conversation` is one chat session. `messages` is the append-only log of everything said — including the AI's tool calls and results. |
| `order.py` | `orders`, `order_items` | An order and its line items. `order_items` snapshots the product name/price at purchase time so history doesn't change if the price later changes. |
| `payment.py` | `payments` | Append-only payment attempts. A failed attempt is never mutated — a retry creates a new row. |
| `fulfillment.py` | `fulfillments` | Delivery records attached to a paid order. Holds a `credential_ref` handle, never the secret. |
| `credential.py` | `product_credentials` | Encrypted, reusable game-account vault (login + Fernet-encrypted password + capacity/allocated). The "secrets manager" `credential_ref` points into. |
| `support_ticket.py` | `support_tickets` | Human escalation tickets. Created by the AI when it can't resolve something, or automatically on double-charges. |
| `knowledge.py` | `knowledge` | Help articles the AI can search to answer customer questions. Has full-text search support just like products. |
| `agent_run.py` | `agent_runs` | Observability: one row per AI turn recording token counts, latency, and cost estimate. |
| `webhook_event.py` | `webhook_events` | Raw inbound webhook log. Written before processing — gives idempotency and a replayable record of every event. |

---

## `app/repositories/` — Data Access Layer

Repositories handle SQL queries. They enforce **tenant isolation** — every query is automatically scoped to the current `business_id` so data from one tenant can never leak to another.

| File | Purpose |
|------|---------|
| `base.py` | `BaseRepository` (tenant-scoped) and `GlobalRepository` (cross-tenant, for tables like `businesses` and `webhook_events`). Provides standard `get`, `list`, `add`, `delete`. |
| `businesses.py` | Look up a business by `slug`. Used by webhook handlers to resolve which tenant owns an inbound message. |
| `products.py` | Product search (full-text + trigram fallback), batch fetch by IDs, category listing. |
| `customers.py` | Resolve a WhatsApp/Telegram handle to a customer row. Eager-loads the customer in one query. |
| `conversations.py` | Get the active conversation for a channel. List messages with oldest-first ordering. |
| `orders.py` | Get order by reference, get latest open order for a customer. |
| `payments.py` | Global payment lookup by provider ID (cross-tenant, used by Razorpay webhook). Get open/successful payments for an order. |
| `fulfillments.py` | Get fulfillment status for an order. |
| `support_tickets.py` | List open tickets, get by reference, find open ticket for a conversation. |
| `knowledge.py` | Full-text search + trigram fallback on help articles. |
| `agent_runs.py` | List runs for a conversation. Aggregate token totals per tenant for cost tracking. |
| `webhook_events.py` | Insert-or-ignore for idempotency (`ON CONFLICT DO NOTHING`). Mark events processed/failed/ignored. |

---

## `app/services/` — Business Logic Layer

Services contain the rules. They use repositories for data access but own all the decision-making.

| File | Purpose |
|------|---------|
| `state_machine.py` | Validates state transitions for orders, payments, fulfillments, and conversations. Raises `InvalidStateTransition` if a transition is illegal (e.g. can't cancel a PAID order). |
| `order_service.py` | Creates orders, prices every line from the DB (the AI never sets a price), merges duplicate items, and handles order cancellation. |
| `payment_service.py` | Creates payment attempts and applies provider outcomes (webhooks). The only place an order becomes PAID. Detects and flags double charges (rule 7). |
| `customer_service.py` | Resolves or creates a customer from an inbound channel handle. Handles the case where the same person messages on both WhatsApp and Telegram. |
| `conversation_service.py` | Manages conversation lifecycle and the append-only message log. Records customer messages, AI replies, tool calls, and tool results. |
| `catalog_service.py` | Product search and single-product lookup. The authoritative price source the AI quotes from. |
| `knowledge_service.py` | Searches the help article knowledge base. Truncates long articles before sending to the AI to save tokens. |
| `support_service.py` | Creates support tickets. Reuses an existing open ticket for the same conversation (prevents duplicate tickets from one frustrated customer). |
| `fulfillment_service.py` | Reads and updates delivery status for fulfilled orders. |
| `credential_service.py` | Stocks the credential vault, allocates a slot atomically, and decrypts a secret for delivery. |
| `delivery_service.py` | On a PAID order, allocates credentials, records the fulfillment (refs only), moves the order to FULFILLED. Idempotent. |
| `notify.py` | Best-effort outbound send of the delivered credentials to the customer's WhatsApp/Telegram. |

---

## `app/agent/` — AI Agent

Everything related to the Claude AI agent.

| File | Purpose |
|------|---------|
| `prompts.py` | Builds the system prompt. Split into a **stable cached half** (business info, rules — survives between turns) and a **volatile half** (current order, conversation state — injected per turn so it doesn't bust the cache). |
| `context.py` | `ToolContext` — the object passed to every tool handler. Holds `business`, `customer`, `conversation` and lazily creates services. The AI has no way to change these — they come from the verified webhook, not from model input. |
| `registry.py` | The fixed, ordered list of all 9 tools exposed to the AI. Order is stable (not a set) to keep the cached prefix byte-identical between requests. |
| `runner.py` | `AgentRunner.run()` — the **Claude** conversation loop. Calls Anthropic API → executes tool calls → records tool_call/result rows → loops until `end_turn` → writes the final reply and an `AgentRun` metrics row. |
| `gemini_runner.py` | `GeminiAgentRunner.run()` — the **Gemini** equivalent (Google `google-genai`). Same tool loop, audit rows, and `AgentRun` metrics; converts the tool JSON-schemas to Gemini function declarations and the message log to Gemini `contents`. |
| `factory.py` | `build_agent_runner(ctx)` — returns the Claude or Gemini runner based on `LLM_PROVIDER`. Lazy imports so only the active provider's SDK is required. |
| `tools/base.py` | `ToolSpec` dataclass (name + description + JSON schema + handler). Helpers for building strict schemas. |
| `tools/catalog.py` | `search_products`, `get_product` — product discovery tools. |
| `tools/orders.py` | `create_order`, `get_order_status`, `cancel_order` — order lifecycle tools. |
| `tools/payments.py` | `create_payment_link`, `check_payment_status` — payment tools. |
| `tools/knowledge.py` | `search_knowledge` — searches help articles for support answers. |
| `tools/support.py` | `create_support_ticket` — escalates to a human agent. |

---

## `app/providers/` — External API Clients

Thin wrappers around third-party APIs. Each client handles auth, retries (via tenacity), and error normalization.

```
providers/
├── razorpay/
│   └── client.py       # create_payment_link() → PaymentLinkResult
│                       # parse_webhook_outcome() → ProviderOutcome
├── whatsapp/
│   └── client.py       # send_text(to, text) → wamid (message ID)
└── telegram/
    └── client.py       # send_message(chat_id, text) → message_id
```

---

## `app/channels/` — Inbound Message Parsers

Parse raw webhook payloads from each messaging platform into a clean `InboundMessage` object.

```
channels/
├── whatsapp/
│   └── parser.py       # parse_webhook(payload) → list[InboundMessage]
│                       # is_status_only(payload) → bool (True = delivery receipts, skip)
└── telegram/
    └── parser.py       # parse_update(payload) → InboundMessage | None
```

---

## `app/api/` — HTTP Route Handlers

```
api/
├── deps.py             # Shared FastAPI dependencies:
│                       #   get_session → DB session
│                       #   require_internal_key → validates X-Internal-Key header
│                       #   get_business(slug) → resolved Business object
│
├── web.py              # POST /web/chat — synchronous test endpoint. Runs the
│                       # full agent pipeline and returns the reply (+ which
│                       # tools ran) in the HTTP response. No WhatsApp/Meta
│                       # setup needed. Key-protected except in local.
│
├── webhooks/           # Public endpoints (no auth — use signature verification)
│   ├── whatsapp.py     # GET (Meta verify challenge) + POST (inbound messages)
│   ├── telegram.py     # POST (inbound Telegram updates)
│   └── razorpay.py     # POST (payment confirmation events)
│
└── admin/              # Internal endpoints (require X-Internal-Key header)
    ├── businesses.py   # CRUD for tenant businesses
    ├── products.py     # CRUD for catalog products per business
    ├── support.py      # List, view, and update support tickets
    ├── customers.py    # List and view customers
    └── knowledge.py    # CRUD for knowledge base articles
```

---

## `migrations/` — Database Migrations

Alembic migration scripts. Run in order to build the full schema.

| File | What it does |
|------|-------------|
| `0001_initial_schema.py` | Creates all 14 tables and 16 Postgres enum types. |
| `0002_search_rls_indexes.py` | Adds GIN (full-text) and trigram indexes on products and knowledge for fast search. |
| `0003_product_credentials.py` | Adds the `product_credentials` vault table and `credential_status` enum. |

Run migrations:
```bash
alembic upgrade head
```

---

## How Data Flows (One Customer Message)

```
Customer (WhatsApp/Telegram)
        │
        ▼
  Webhook Handler          ← verifies signature, records webhook_event
  (api/webhooks/)
        │
        ▼
  Channel Parser           ← extracts message text, sender ID
  (channels/)
        │
        ▼
  Customer Service         ← resolve/create customer + conversation
  (services/customer_service.py)
        │
        ▼
  Agent Runner             ← calls Claude with tools
  (agent/runner.py)
        │
   ┌────┴────┐
   │  Tools  │            ← tools call services; services call repositories
   └────┬────┘
        │
        ▼
  Provider Client          ← sends reply back (WhatsApp/Telegram)
  (providers/)
```
