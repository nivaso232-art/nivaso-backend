# Nivaso Backend — Database Schema Documentation

## Table of Contents

1. [Overview](#overview)
2. [Entity Relationship Map](#entity-relationship-map)
3. [Table-by-Table Reference](#table-by-table-reference)
   - [businesses](#1-businesses)
   - [products](#2-products)
   - [customers](#3-customers)
   - [customer_channels](#4-customer_channels)
   - [conversations](#5-conversations)
   - [messages](#6-messages)
   - [orders](#7-orders)
   - [order_items](#8-order_items)
   - [payments](#9-payments)
   - [fulfillments](#10-fulfillments)
   - [support_tickets](#11-support_tickets)
   - [knowledge](#12-knowledge)
   - [agent_runs](#13-agent_runs)
   - [webhook_events](#14-webhook_events)
4. [Key Design Patterns](#key-design-patterns)

---

## Overview

The schema has **14 tables** built around one central concept: a **business** (tenant) sells things to **customers** over chat (WhatsApp / Telegram). Every table either describes what the business sells, who the customers are, what they ordered, or what happened during their conversation with the AI.

| Group | Tables | What they cover |
|-------|--------|-----------------|
| **Tenant** | `businesses` | The company using Nivaso |
| **Catalog** | `products` | What the business sells |
| **Identity** | `customers`, `customer_channels` | Who the customers are and how they contact the business |
| **Conversation** | `conversations`, `messages` | The chat history |
| **Commerce** | `orders`, `order_items`, `payments`, `fulfillments` | Buying, paying, delivering |
| **Support** | `support_tickets` | Human escalations |
| **Knowledge** | `knowledge` | Help articles the AI uses to answer questions |
| **Observability** | `agent_runs`, `webhook_events` | What the AI did and what events arrived |

---

## Entity Relationship Map

```
                         ┌─────────────┐
                         │  businesses │  ← Tenant root. Everything hangs off this.
                         └──────┬──────┘
              ┌─────────────────┼─────────────────┐
              │                 │                 │
              ▼                 ▼                 ▼
         ┌─────────┐      ┌──────────┐      ┌───────────┐
         │products │      │customers │      │ knowledge │
         └────┬────┘      └────┬─────┘      └───────────┘
              │                │
              │         ┌──────┴──────────┐
              │         │                 │
              │    ┌────▼───────┐   ┌─────▼──────┐
              │    │  customer  │   │conversations│
              │    │  _channels │   └─────┬───────┘
              │    └────────────┘         │
              │                     ┌────▼────┐
              │                     │messages │
              │                         
              │    ┌───────────────────────────┐
              └───►│         orders            │
                   └──┬────────────────────────┘
                      │
          ┌───────────┼───────────┐
          │           │           │
          ▼           ▼           ▼
    ┌───────────┐ ┌────────┐ ┌──────────────┐
    │order_items│ │payments│ │ fulfillments │
    └───────────┘ └────────┘ └──────────────┘

support_tickets → customers, orders, conversations
agent_runs      → conversations
webhook_events  → businesses (nullable, set after parsing)
```

---

## Table-by-Table Reference

---

### 1. `businesses`

**What it is:** The tenant root. One row = one business using Nivaso.

**Why it exists:** Nivaso is multi-tenant. Every other table traces back to a `business_id` so one business's data is completely isolated from another's.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `slug` | VARCHAR(64) | URL-safe unique name, e.g. `"gameszone-chennai"`. Used in admin API paths and to route inbound webhooks to the right tenant. |
| `name` | VARCHAR(255) | Display name, injected into the AI's system prompt so it introduces itself correctly. |
| `description` | TEXT | Optional description of the business. |
| `timezone` | VARCHAR(64) | Business timezone (default `Asia/Kolkata`). Used for scheduling and reporting. |
| `status` | ENUM | `active` / `suspended` / `inactive`. A suspended business stops accepting new orders immediately — checked on every inbound webhook. |
| `settings` | JSONB | Flexible per-tenant config: agent tone, supported languages, business hours, escalation rules. Anything that doesn't deserve its own column yet. |

**Links to other tables:**
- `businesses` ← `products` (a business owns its catalog)
- `businesses` ← `customers` (customers are per-business, not global)
- `businesses` ← `knowledge` (help articles are per-business)
- `businesses` ← `orders`, `payments`, `conversations`, etc. (via `business_id` on every table)

---

### 2. `products`

**What it is:** The catalog — one row per item the business sells.

**Why it exists:** The AI must quote and sell real products with real prices. All pricing comes from this table; the AI cannot invent or override a price.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `sku` | VARCHAR(64) | Optional internal product code. Unique per business (partial: allows multiple NULLs). |
| `name` | VARCHAR(255) | Product display name. Searchable. |
| `description` | TEXT | Full description. Searchable at lower weight than name. |
| `price` | NUMERIC(12,2) | The authoritative price. Orders always re-read this — a stale quote in chat cannot become a stale price in the ledger. |
| `currency` | VARCHAR(3) | Currency code, e.g. `INR`. |
| `status` | ENUM | `active` / `inactive` / `out_of_stock` / `archived`. Only `active` products can be ordered. |
| `category` | VARCHAR(128) | Groups products (e.g. `"Game"`, `"Apartment"`). Used for filtering and shown in the AI's system prompt as orientation. |
| `metadata_` | JSONB | Flexible attributes: `{platform: "PC", edition: "Standard"}` for games, `{bhk: 2, locality: "Adyar"}` for real estate. |
| `search_doc` | TSVECTOR | **Generated column** (never written by the app). Postgres computes a weighted full-text document from name (A), category + sku (B), description (C). Powers keyword search. |

**Links to other tables:**
- `products` → `businesses` (belongs to one business)
- `products` ← `order_items` (products appear in orders — but as a nullable FK so deleting a product doesn't delete history)

**Why `search_doc` is generated:**
The column is `STORED` (persisted on disk), so search queries hit an index rather than computing the vector per row. A GIN index on `search_doc` makes full-text search fast even on large catalogs.

---

### 3. `customers`

**What it is:** A unique person per business. One row = one human customer.

**Why it exists:** Split from `customer_channels` so the same person on WhatsApp and Telegram is still one customer — with one order history and one support queue.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `name` | VARCHAR(255) | Display name, pulled from the messaging platform on first contact. |
| `phone` | VARCHAR(32) | Normalized E.164 phone number (e.g. `+919876543210`). Unique per business (partial: multiple NULLs allowed for Telegram-only users who have no phone). |
| `email` | VARCHAR(320) | Optional email. Not populated by the AI — reserved for future admin input. |
| `metadata_` | JSONB | Flexible extra info: loyalty tier, notes from human agents, etc. |

**Links to other tables:**
- `customers` → `businesses`
- `customers` ← `customer_channels` (how they reach the business)
- `customers` ← `conversations`
- `customers` ← `orders`
- `customers` ← `support_tickets`

**Why phone has a partial unique index:**
A Telegram-only user has no phone number, so the column is nullable. But if a phone number is known, it must be unique per business — otherwise the same person on two channels might be created as two separate customers.

---

### 4. `customer_channels`

**What it is:** A messaging handle — one row per "way a customer contacts the business."

**Why it exists:** One customer can message from WhatsApp AND Telegram. This table stores each handle separately, pointing back to one `customers` row. It decouples the person (customers) from the platform identity (this table).

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `customer_id` | UUID → `customers` | The person this handle belongs to |
| `channel` | ENUM | `whatsapp` / `telegram` / `web` |
| `external_user_id` | VARCHAR(128) | The platform's own ID. WhatsApp: the `wa_id` (phone number without `+`). Telegram: the numeric chat/user ID. |
| `display_name` | VARCHAR(255) | Name as it appears on the platform (e.g. WhatsApp profile name). |
| `metadata_` | JSONB | Platform-specific extras. |

**Links to other tables:**
- `customer_channels` → `businesses`
- `customer_channels` → `customers`
- `customer_channels` ← `conversations` (each conversation belongs to one channel identity)

**Unique constraint:** `(business_id, channel, external_user_id)` — ensures one channel handle maps to exactly one customer.

---

### 5. `conversations`

**What it is:** One active chat session between a customer and the AI. One row = one open thread.

**Why it exists:** Conversations track the current state of a chat (are we browsing? waiting for payment? escalated to human?) and group all messages together.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `customer_id` | UUID → `customers` | The person chatting |
| `customer_channel_id` | UUID → `customer_channels` | Which handle/platform this conversation is happening on |
| `channel` | ENUM | Denormalized copy of the channel for fast querying without a join |
| `external_conversation_id` | VARCHAR(128) | Platform's thread ID, if it has one |
| `status` | ENUM | `active` / `closed` |
| `current_state` | VARCHAR(48) | Narrative state: `NEW`, `PRODUCT_ENQUIRY`, `WAITING_CONFIRMATION`, `PAYMENT_PENDING`, `PAYMENT_VERIFICATION`, `FULFILLMENT`, `SUPPORT`, `HUMAN_HANDOFF`, `COMPLETED`. Stored as text (not a Postgres enum) because this evolves during development and is not a money-state. |
| `last_message_at` | TIMESTAMPTZ | Updated on every message. Used to sort the active conversation queue by recency. |
| `metadata_` | JSONB | Any extra state the system needs to track per-conversation. |

**Links to other tables:**
- `conversations` → `businesses`, `customers`, `customer_channels`
- `conversations` ← `messages`
- `conversations` ← `orders` (an order may be linked to the conversation that created it)
- `conversations` ← `support_tickets`
- `conversations` ← `agent_runs`

**Why only one active conversation per channel (`uq_conversations_active_per_channel`):**
Meta delivers WhatsApp webhooks in batches, so two messages can arrive simultaneously. Without this constraint, two handlers could both find "no active conversation" and each create one, splitting the chat into two parallel threads. The database constraint ensures exactly one wins — the other reads what the winner wrote.

---

### 6. `messages`

**What it is:** The append-only chat log. Every message ever sent or received, plus every tool call the AI made, is a row here.

**Why it exists:** This is the full audit trail. "Why did the AI quote ₹229?" is answerable by replaying the messages. It is also replayed into the Anthropic API on every turn so the AI remembers the conversation.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `conversation_id` | UUID → `conversations` | The conversation this message belongs to |
| `seq` | BIGINT IDENTITY | Monotonic sequence number. Guarantees correct ordering even when multiple messages share the same millisecond timestamp (e.g. several tool calls in one agent turn). |
| `sender_type` | ENUM | `customer` / `assistant` / `tool` / `system` / `agent` (human agent) |
| `message_type` | ENUM | `text` / `image` / `audio` / `video` / `document` / `location` / `tool_call` / `tool_result` / `system_note` |
| `status` | ENUM | `received` / `pending` / `sent` / `delivered` / `read` / `failed` |
| `content` | TEXT | Human-readable text. NULL for tool_call/tool_result rows (substance is in `payload`). |
| `payload` | JSONB | Tool call: `{"tool": "search_products", "arguments": {...}}`. Tool result: `{"tool": "search_products", "result": {...}, "is_error": false}`. Media: `{"storage_path": "...", "mime_type": "..."}`. |
| `external_message_id` | VARCHAR(128) | The platform's own message ID (WhatsApp `wamid`). Used for idempotency — if Meta redelivers the same message, it is silently ignored. |
| `tool_use_id` | VARCHAR(64) | The Anthropic tool_use block ID. Links a `tool_call` row to its `tool_result` row so a turn can be fully reconstructed. |

**Links to other tables:**
- `messages` → `businesses`, `conversations`

**Why `seq` instead of `created_at` for ordering:**
Several messages in one agent turn (tool_call, tool_result, assistant reply) can be written within the same millisecond. `created_at` alone cannot order them correctly; `seq` is a database-level identity that is strictly monotonic.

---

### 7. `orders`

**What it is:** A purchase — one row = one customer's order.

**Why it exists:** Centralizes the commercial transaction. An order is the bridge between a conversation (customer said "I want GTA 5") and a payment (Razorpay confirmed the money).

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `customer_id` | UUID → `customers` | Who placed the order |
| `conversation_id` | UUID → `conversations` (nullable) | The conversation that created this order. Nullable because orders can also be created by admin APIs. |
| `reference` | VARCHAR(32) | Human-readable ID like `ORD-2608-7F3K9Q`. This is what the customer quotes to support. Unique per business. |
| `status` | ENUM | `DRAFT` → `PENDING_CONFIRMATION` → `PAYMENT_PENDING` → `PAID` → `FULFILLED` (happy path). Also: `PAYMENT_FAILED`, `CANCELLED`, `REFUNDED`. |
| `currency` | VARCHAR(3) | Order currency (inherited from products). |
| `subtotal` | NUMERIC(12,2) | Sum of all line item totals before discount. |
| `discount` | NUMERIC(12,2) | Discount applied (default 0). |
| `total` | NUMERIC(12,2) | `subtotal - discount`. DB constraint: `total = subtotal - discount`. |
| `metadata_` | JSONB | Extra info: `cancellation_reason`, any notes from the AI or admin. |

**Links to other tables:**
- `orders` → `businesses`, `customers`, `conversations`
- `orders` ← `order_items` (the products in this order)
- `orders` ← `payments` (payment attempts for this order)
- `orders` ← `fulfillments` (delivery records)
- `orders` ← `support_tickets` (tickets about this order)

**Why separate `subtotal`, `discount`, `total`:**
A DB constraint enforces `total = subtotal - discount`. This means the database will reject any row where the numbers don't add up — an accounting error cannot be silently inserted.

**Terminal statuses (`FULFILLED`, `CANCELLED`, `REFUNDED`):**
Once an order reaches a terminal status, no further transitions are allowed. This is checked in the state machine before every status update.

---

### 8. `order_items`

**What it is:** Line items within an order. One row = one product line (product × quantity × price).

**Why it exists:** An order can have multiple products. Also, prices can change — the snapshot columns freeze the price at the moment of purchase so the order history is accurate forever.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `order_id` | UUID → `orders` | The order this line belongs to |
| `product_id` | UUID → `products` (nullable, `SET NULL`) | Points to the catalog. Nullable: if the product is later deleted, this becomes NULL but the line item still exists with its snapshots. |
| `product_name` | VARCHAR(255) | **Snapshot** — product name at purchase time |
| `product_sku` | VARCHAR(64) | **Snapshot** — SKU at purchase time |
| `unit_price` | NUMERIC(12,2) | **Snapshot** — price at purchase time (not the current catalog price) |
| `quantity` | INT | How many units |
| `total` | NUMERIC(12,2) | `unit_price × quantity`. DB constraint: `total = unit_price * quantity`. |

**Links to other tables:**
- `order_items` → `orders` (CASCADE delete: deleting an order deletes its items)
- `order_items` → `products` (SET NULL: deleting a product makes this NULL, not cascade)

**Why `product_id` is SET NULL instead of CASCADE:**
If a business deletes a product from their catalog, past sales of that product must remain readable. The snapshot columns (`product_name`, `product_sku`, `unit_price`) preserve the record even after the product row is gone.

---

### 9. `payments`

**What it is:** Payment attempts — **append-only**. One row = one attempt to pay for an order.

**Why it exists:** Money is the most important thing to get right. Payments are append-only (a failed attempt is never updated to "succeeded" — a retry creates a new row) and every status change comes only from a verified Razorpay webhook.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `order_id` | UUID → `orders` | The order being paid |
| `provider` | ENUM | `razorpay` / `manual` |
| `provider_payment_id` | VARCHAR(128) | Razorpay's `pay_xxx` ID. Set when money actually moves. |
| `provider_order_id` | VARCHAR(128) | Razorpay's `order_xxx` ID (if used). |
| `provider_payment_link_id` | VARCHAR(128) | Razorpay's `plink_xxx` ID. Set when the payment link is created — this is how we match an incoming webhook back to the pending attempt. |
| `payment_url` | TEXT | The short URL sent to the customer. |
| `amount` | NUMERIC(12,2) | The amount this attempt was for (copied from `order.total`). |
| `currency` | VARCHAR(3) | Payment currency. |
| `status` | ENUM | `PENDING` → `SUCCESS` or `FAILED` / `CANCELLED` / `REFUNDED` |
| `failure_reason` | TEXT | Provider's failure description, if any. |
| `is_duplicate` | BOOLEAN | True if a second successful payment arrived for an already-paid order. The row is recorded truthfully but flagged. |
| `needs_refund` | BOOLEAN | True if this payment needs to be refunded (either a duplicate charge or an amount mismatch). Indexed so the refund queue is a single fast query. |
| `raw_payload` | JSONB | The verbatim Razorpay webhook payload. The reconciliation record if a customer disputes what happened. |

**Links to other tables:**
- `payments` → `businesses`, `orders`

**Why append-only:**
If a payment fails, the row stays FAILED permanently. The customer's next attempt creates a brand new row. This gives a complete audit trail: "tried, insufficient funds, tried again, succeeded" — rather than a single row that was silently mutated to look like it always worked.

**Why `is_duplicate` exists:**
Rule 7 — if Razorpay sends a second SUCCESS for an already-paid order (e.g. the customer tapped "pay" twice), we record it truthfully as SUCCESS (because the money did move) but flag it `is_duplicate = true` and `needs_refund = true`. It does NOT re-mark the order paid, and a support ticket is automatically created for refund processing.

**Cross-tenant lookup:**
`get_by_provider_payment_id` and `get_by_provider_link_id` deliberately do NOT filter by `business_id`. A Razorpay webhook arrives with no tenant context — we find the payment first, read its `business_id`, then scope everything downstream to that tenant.

---

### 10. `fulfillments`

**What it is:** Delivery records for paid orders.

**Why it exists:** After an order is PAID, someone (a human or an automated system) delivers the product and records it here. The AI can check this table to answer "has my game been delivered?"

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `order_id` | UUID → `orders` | The order being fulfilled |
| `status` | ENUM | `PENDING` → `READY` → `DELIVERED` / `FAILED` |
| `credential_ref` | VARCHAR(255) | A reference to a secret (e.g. a game key or a download link stored in a vault). Never the credential itself. |
| `metadata_` | JSONB | Delivery details: tracking number, delivery method, notes. |

**Links to other tables:**
- `fulfillments` → `businesses`, `orders`

**Why `credential_ref` instead of storing the credential:**
Digital products (game keys, license codes) must never be stored in plaintext in a queryable database column. `credential_ref` stores a pointer (e.g. a Vault key path or a secret ID) so the actual secret lives in a secrets manager.

---

### 11. `support_tickets`

**What it is:** Escalations to a human agent — one row = one support case.

**Why it exists:** The AI cannot do everything (it cannot issue refunds, mark things delivered, or solve problems it hasn't seen before). When it hits a wall, it creates a ticket and tells the customer a human will follow up.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `customer_id` | UUID → `customers` | The customer with the problem |
| `conversation_id` | UUID → `conversations` (nullable) | The conversation that triggered this ticket |
| `order_id` | UUID → `orders` (nullable) | Related order, if the problem concerns a specific order |
| `reference` | VARCHAR(32) | Human-readable ID like `TKT-2608-4M2XQ8`. Shown to the customer so they can quote it. |
| `status` | ENUM | `OPEN` → `IN_PROGRESS` → `WAITING_CUSTOMER` → `RESOLVED` → `CLOSED` |
| `priority` | ENUM | `LOW` / `MEDIUM` / `HIGH` / `URGENT`. Priority is raised but never lowered when reusing an existing ticket. |
| `assigned_to` | VARCHAR(128) | Handle of the human agent handling this (email or username). Nullable until assigned. |
| `reason` | VARCHAR(64) | Category: `PAYMENT_PROBLEM`, `REFUND_REQUEST`, `DOUBLE_PAYMENT`, `DELIVERY_DELAY`, etc. Controlled vocabulary so the queue is filterable. |
| `summary` | TEXT | 2-3 sentence description for the human agent: what the customer wants, what the AI already tried, what's blocked. Written in English even if the conversation was in Tamil/Tanglish. |
| `metadata_` | JSONB | Additional notes appended when a duplicate ticket would have been created (e.g. the customer escalated again with more info). |

**Links to other tables:**
- `support_tickets` → `businesses`, `customers`
- `support_tickets` → `conversations` (SET NULL — closing a conversation doesn't delete tickets)
- `support_tickets` → `orders` (SET NULL — cancelling an order doesn't delete tickets)

**Why reuse an existing ticket:**
A frustrated customer sending "still not fixed!!" four times in a row should produce one ticket, not four. When a new escalation arrives for the same conversation, the existing open ticket gets the additional note appended and its priority raised if needed — not a duplicate created.

---

### 12. `knowledge`

**What it is:** The help article knowledge base — one row = one published article the AI can search to answer support questions.

**Why it exists:** Instead of hardcoding answers in the AI's system prompt, support knowledge lives in this table. The AI searches it when a customer has a problem. The business can add/update articles without changing code.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope — each business has its own knowledge base |
| `title` | VARCHAR(255) | Article title, e.g. "How to launch the game after purchase". Searchable at highest weight. |
| `content` | TEXT | Full article body. Sent to the AI (truncated to 1500 chars to save tokens). |
| `source` | VARCHAR(255) | Where this article came from: `"manual"`, a URL, a document name. For auditing. |
| `keywords` | VARCHAR[] | Curated list of extra search terms: Tanglish phrases, slang, common misspellings. The tuning knob for search recall without retraining. |
| `status` | ENUM | `draft` / `published` / `archived`. Only `published` articles appear in search results. |
| `metadata_` | JSONB | Flexible extra fields. |
| `search_doc` | TSVECTOR | **Generated column**. Weighted full-text doc: title (A) + keywords (B) + content (C). Same search architecture as `products`. |

**Links to other tables:**
- `knowledge` → `businesses`

**Why `keywords` is a separate column:**
Postgres full-text search works on English words. A customer writing "game launch aagala" (Tamil for "the game won't start") would never match the English article "How to launch the game". The AI is instructed to translate before searching, but `keywords` adds the Tanglish/slang terms directly so even a raw search can hit. It is the manual tuning knob that a vector store would otherwise provide automatically.

---

### 13. `agent_runs`

**What it is:** One row per AI agent turn — the observability ledger.

**Why it exists:** To answer operational questions that only matter once there's real traffic: How much does a conversation cost? Is prompt caching actually working? How many tool calls does a typical purchase take? Did the loop get cut off before finishing?

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `business_id` | UUID → `businesses` | Tenant scope |
| `conversation_id` | UUID → `conversations` (nullable, SET NULL) | The conversation this turn belongs to. SET NULL so closing a conversation doesn't lose the run history. |
| `model` | VARCHAR(64) | Which model was used (e.g. `claude-opus-5`). |
| `effort` | VARCHAR(16) | Effort level used (`low` / `medium` / `high` / etc.) |
| `input_tokens` | INT | Tokens sent to the model in this turn. |
| `output_tokens` | INT | Tokens generated by the model. |
| `cache_read_tokens` | INT | Tokens served from prompt cache (billed at ~10% of input rate). If this stays at 0, the cache is broken. |
| `cache_creation_tokens` | INT | Tokens written to the prompt cache (billed at 125% of input rate — a one-time cost for future savings). |
| `iterations` | INT | How many times the tool-call loop ran. Equal to `AGENT_MAX_ITERATIONS` means the loop was cut off rather than finishing naturally — worth alerting on. |
| `tool_calls` | INT | Total number of tool executions in this turn. |
| `stop_reason` | VARCHAR(32) | `end_turn` (normal), `max_tokens`, `tool_use` (cut off mid-loop). |
| `latency_ms` | INT | Wall-clock time for the entire turn (ms). |
| `error` | TEXT | Error message if the turn failed. |
| `metadata_` | JSONB | Anthropic request IDs, useful for reporting bad responses to Anthropic. |

**Links to other tables:**
- `agent_runs` → `businesses`, `conversations`

**Computed property — `estimated_usd`:**
A Python property (not a DB column) estimates the dollar cost of a turn based on token counts and list prices. Used for in-dashboard cost estimates; billing truth lives in the Anthropic console.

---

### 14. `webhook_events`

**What it is:** A raw log of every inbound webhook — one row = one event received from WhatsApp, Telegram, or Razorpay.

**Why it exists:** Three reasons, all critical:

1. **Idempotency** — Meta and Razorpay both retry webhooks aggressively on any non-2xx response. Without this table, a slow agent turn would trigger duplicate message processing. The unique constraint on `(source, external_event_id)` ensures exactly one handler processes each event — the database arbitrates, not the application code.

2. **Fast acknowledgment** — The handler verifies the signature, writes this row, and returns 200 immediately. The AI turn happens in a background task. This means a 10-second agent turn never causes duplicate delivery.

3. **Replayability** — `payload` is the verbatim raw body. If processing fails (a bug, a database error), the event can be replayed against fixed code without reconstructing it from log lines.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | Primary key |
| `source` | ENUM | `whatsapp` / `telegram` / `razorpay` |
| `external_event_id` | VARCHAR(191) | The provider's event ID. WhatsApp has no per-envelope ID, so we synthesise one from the first message's `wamid`. |
| `business_id` | UUID → `businesses` (nullable) | NULL until the payload is parsed and resolved to a tenant. Nullable because we write this row BEFORE parsing — an unparseable payload must still be recorded. |
| `signature_verified` | BOOLEAN | Always true in practice (the handler rejects before writing if verification fails). Stored for auditing and for any future "log but don't trust" mode. |
| `payload` | JSONB | The verbatim request body — the full replay record. |
| `status` | ENUM | `received` → `processing` → `processed` / `failed` / `ignored` |
| `error` | TEXT | Truncated error message (max 2000 chars) if processing failed. For triage — the `payload` column is the full forensic record. |
| `processed_at` | TIMESTAMPTZ | When the event finished processing. |
| `attempts` | INT | How many processing attempts were made (incremented on each retry). |

**Links to other tables:**
- `webhook_events` → `businesses` (nullable FK, SET NULL)

**Not tenant-scoped at write time:**
This is the only table where `business_id` is nullable and populated after the fact. The event arrives with no tenant context — we record it first (for idempotency), then parse it to find which business it belongs to, then update the row.

---

## Key Design Patterns

### 1. Tenant Isolation
Every table except `businesses` and `webhook_events` carries a `business_id` column with an index. All repository queries are automatically scoped to the current tenant. A query that forgets the tenant filter returns an empty result — not another tenant's data.

### 2. Append-Only Tables
`messages`, `payments`, and `webhook_events` are never updated after insert. Failed records stay failed; retries create new rows. This makes the audit trail tamper-proof and makes redelivery safe.

### 3. Price Snapshots in `order_items`
`product_name`, `product_sku`, and `unit_price` are copied at purchase time. If the business later changes a product's price or name (or even deletes it), every past order still shows exactly what was sold at what price. History is never rewritten.

### 4. State Machines
`orders`, `payments`, `fulfillments`, and `conversations` all have a `status` or `current_state` column. Transitions are validated in `app/services/state_machine.py` before any update. An invalid transition (e.g. `PAID → DRAFT`) raises an error — it is impossible to silently regress an order's state.

### 5. Idempotency Keys
Multiple unique constraints protect against duplicate processing:
- `uq_webhook_events_source_external_event_id` — prevents duplicate webhook processing
- `uq_messages_conversation_id_external_message_id` — prevents the same WhatsApp message being recorded twice
- `uq_payments_provider_provider_payment_id` — prevents the same Razorpay payment being applied twice

### 6. Full-Text Search Without Vectors
`products.search_doc` and `knowledge.search_doc` are generated `TSVECTOR` columns. A GIN index makes keyword search fast. A trigram index on `name`/`title` handles typos. The AI translates customer messages from Tanglish to English before searching, so English FTS is sufficient.

### 7. No Secrets in the Database
`fulfillments.credential_ref` stores a reference (a vault key path, a secret ID) — never the actual game key or license code. The real credential lives in a secrets manager.
