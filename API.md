# Nivaso API Documentation

## Contents

- [Base URL and Formats](#base-url-and-formats)
- [Authentication](#authentication)
- [Error Responses](#error-responses)
- [Enums Reference](#enums-reference)
- [Webhook Endpoints](#webhook-endpoints)
  - [WhatsApp — Verification](#whatsapp--verification-handshake)
  - [WhatsApp — Inbound Messages](#whatsapp--inbound-messages)
  - [Telegram — Inbound Updates](#telegram--inbound-updates)
  - [Razorpay — Payment Events](#razorpay--payment-events)
- [Admin — Businesses](#admin--businesses)
- [Admin — Products](#admin--products)
- [Admin — Support Tickets](#admin--support-tickets)
- [Admin — Customers](#admin--customers)
- [Admin — Knowledge Base](#admin--knowledge-base)
- [Health](#health)

---

## Base URL and Formats

```
http://localhost:8000        # local development
https://your-domain.com      # production
```

- All request and response bodies are **JSON** (`Content-Type: application/json`)
- All IDs are **UUID strings** (`"3fa85f64-5717-4562-b3fc-2c963f66afa6"`)
- All monetary amounts are **decimal strings** (`"229.00"`) — never floats
- Timestamps follow **ISO 8601** (`"2026-08-31T10:30:00+00:00"`)

---

## Authentication

### Webhook endpoints
No API key. Each provider signs its payload with HMAC-SHA256. The server verifies the signature before processing.

| Provider | Header | Format |
|----------|--------|--------|
| WhatsApp | `X-Hub-Signature-256` | `sha256=<hex>` |
| Telegram | `X-Telegram-Bot-Api-Secret-Token` | `<token>` (exact string match) |
| Razorpay | `X-Razorpay-Signature` | `<hex>` |

Requests with missing or invalid signatures receive `401 Unauthorized`.

### Admin endpoints
All `/admin/*` routes require:

```
X-Internal-Key: <your-INTERNAL_API_KEY>
```

Requests without this header receive `401 Unauthorized`.

---

## Error Responses

All errors follow the same envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Product not found.",
    "details": {
      "product_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    }
  }
}
```

| HTTP Status | `code` | When |
|-------------|--------|------|
| `400` | `app_error` | General business rule violation |
| `401` | `unauthorized` | Missing or invalid API key / webhook signature |
| `401` | `invalid_signature` | Webhook HMAC mismatch |
| `404` | `not_found` | Resource does not exist or belongs to another tenant |
| `409` | `conflict` | State conflict (e.g. cancelling a paid order) |
| `409` | `invalid_state_transition` | Illegal status change |
| `422` | `validation_error` | Request body failed schema validation |
| `502` | `provider_error` | Upstream provider (Razorpay, Meta, Telegram) failed |
| `500` | `internal_error` | Unexpected server error |

---

## Enums Reference

### `BusinessStatus`
`active` · `suspended` · `inactive`

### `ProductStatus`
`active` · `inactive` · `out_of_stock` · `archived`

### `OrderStatus`
`DRAFT` · `PENDING_CONFIRMATION` · `PAYMENT_PENDING` · `PAYMENT_FAILED` · `PAID` · `FULFILLED` · `CANCELLED` · `REFUNDED`

### `PaymentStatus`
`PENDING` · `PROCESSING` · `SUCCESS` · `FAILED` · `CANCELLED` · `REFUNDED`

### `FulfillmentStatus`
`PENDING` · `READY` · `DELIVERED` · `FAILED`

### `TicketStatus`
`OPEN` · `IN_PROGRESS` · `WAITING_CUSTOMER` · `RESOLVED` · `CLOSED`

### `TicketPriority`
`LOW` · `MEDIUM` · `HIGH` · `URGENT`

### `KnowledgeStatus`
`draft` · `published` · `archived`

### `TicketReason` (agent-settable)
`PRODUCT_ACCESS_PROBLEM` · `PAYMENT_PROBLEM` · `REFUND_REQUEST` · `DELIVERY_DELAY` · `AI_COULD_NOT_RESOLVE` · `CUSTOMER_REQUESTED_HUMAN` · `OTHER`

---

## Web Test Endpoint

A synchronous endpoint for driving the agent without WhatsApp/Telegram or any
Meta/Razorpay setup. It runs the **same** pipeline as the webhooks (resolve
business → customer → conversation → run the agent) but returns the reply
directly in the HTTP response, along with which tools the agent used.

```
POST /web/chat
```

**Auth:** requires the `X-Internal-Key` header — **except in local** (`APP_ENV=local`),
where it is disabled for convenience.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | yes | The customer's message |
| `user_id` | string | no | Stable id for this tester. Same id = same conversation. Defaults to `web-tester`. |
| `business_slug` | string | yes | Which tenant to talk to. Required — there is no default. |
| `display_name` | string | no | Optional display name for the customer |

**Success response — `200 OK`**

```json
{
  "reply": "Hi! Yes, we have GTA 5 Premium Edition for PC, it costs INR 299.00.",
  "business_slug": "default",
  "conversation_id": "a65b6df5-b7c6-4ffd-bc2b-aee5c1ac6473",
  "customer_id": "64a169a0-18cd-4921-80db-f98d9feb7ad8",
  "conversation_state": "NEW",
  "tools_used": [
    { "tool": "search_products", "arguments": { "query": "GTA 5" } }
  ]
}
```

`tools_used` lists the actions the agent took this turn — useful for verifying
behaviour. The reply is produced by whichever provider `LLM_PROVIDER` selects
(Claude or Gemini).

---

## Webhook Endpoints

Webhook routes are **public** (no API key). Each verifies the provider signature and returns `200` immediately. All actual processing happens in a background task so slow agent turns never cause provider retries.

---

### WhatsApp — Verification Handshake

Meta calls this once when you register the webhook URL in the Meta App Dashboard.

```
GET /webhooks/whatsapp
```

**Query parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `hub.mode` | string | Must be `"subscribe"` |
| `hub.verify_token` | string | Must match `WHATSAPP_VERIFY_TOKEN` in your `.env` |
| `hub.challenge` | string | Random string Meta wants echoed back |

**Success response — `200 OK`**

```
Content-Type: text/plain

<hub.challenge value>
```

**Failure response — `403 Forbidden`**

Returned when `hub.verify_token` does not match. Plain empty body.

---

### WhatsApp — Inbound Messages

Meta sends this for every customer message and every delivery/read status update.

```
POST /webhooks/whatsapp
```

**Required header**

```
X-Hub-Signature-256: sha256=<hmac-hex>
```

**Request body** — Meta's standard webhook envelope (sent by Meta, not by you):

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "919876543210",
              "phone_number_id": "PHONE_NUMBER_ID"
            },
            "contacts": [
              {
                "profile": { "name": "Rajesh Kumar" },
                "wa_id": "919876543210"
              }
            ],
            "messages": [
              {
                "from": "919876543210",
                "id": "wamid.abc123",
                "timestamp": "1693000000",
                "type": "text",
                "text": { "body": "bro GTA 5 irukka?" }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

**Response — `200 OK`**

Empty body. Always returned immediately after signature verification. Processing happens in the background.

**What happens in the background:**

1. Status-only payloads (delivery receipts, read receipts) are silently ignored.
2. The event is recorded to `webhook_events` for idempotency. Duplicate deliveries are no-ops.
3. Customer and conversation are resolved or created.
4. The AI agent runs and sends a reply back via the WhatsApp Cloud API.

---

### Telegram — Inbound Updates

Telegram sends this for every customer message sent to the bot.

```
POST /webhooks/telegram
```

**Required header**

```
X-Telegram-Bot-Api-Secret-Token: <your-TELEGRAM_WEBHOOK_SECRET>
```

**Request body** — Telegram's standard Update object (sent by Telegram, not by you):

```json
{
  "update_id": 987654321,
  "message": {
    "message_id": 42,
    "from": {
      "id": 78456321,
      "first_name": "Arun",
      "last_name": "Kumar",
      "username": "arun_k"
    },
    "chat": {
      "id": 78456321,
      "type": "private"
    },
    "date": 1693000000,
    "text": "hi price pesuvoma"
  }
}
```

**Response — `200 OK`**

Empty body. Always returned immediately. Processing is in the background.

**What happens in the background:**

Same as WhatsApp — customer resolution, conversation management, agent turn, reply via Telegram Bot API.

---

### Razorpay — Payment Events

Razorpay sends this when a customer pays (or when a payment fails).

```
POST /webhooks/razorpay
```

**Required header**

```
X-Razorpay-Signature: <hmac-hex>
```

**Request body** — Razorpay's webhook payload (sent by Razorpay, not by you).

**Event: `payment_link.paid`** — customer successfully paid

```json
{
  "entity": "event",
  "account_id": "acc_xxx",
  "event": "payment_link.paid",
  "contains": ["payment_link", "payment"],
  "payload": {
    "payment_link": {
      "entity": {
        "id": "plink_xxx",
        "amount": 22900,
        "currency": "INR",
        "reference_id": "ORD-2608-7F3K9Q",
        "status": "paid"
      }
    },
    "payment": {
      "entity": {
        "id": "pay_xxx",
        "amount": 22900,
        "currency": "INR",
        "status": "captured",
        "payment_link_id": "plink_xxx"
      }
    }
  },
  "id": "evt_xxx",
  "created_at": 1693000000
}
```

**Event: `payment.failed`** — payment attempt failed

```json
{
  "entity": "event",
  "event": "payment.failed",
  "payload": {
    "payment": {
      "entity": {
        "id": "pay_yyy",
        "amount": 22900,
        "currency": "INR",
        "status": "failed",
        "payment_link_id": "plink_xxx",
        "error_description": "Your payment was declined by the bank."
      }
    }
  },
  "id": "evt_yyy"
}
```

**Response — `200 OK`**

Empty body. Always returned immediately after signature verification.

**What happens in the background:**

1. The event is recorded for idempotency. Duplicate Razorpay retries are no-ops.
2. The payment attempt is located by `provider_payment_link_id` (cross-tenant lookup).
3. `PaymentService.apply_provider_outcome()` is called — the **only place** an order transitions to `PAID`.
4. If the payment is a double-charge, `is_duplicate` and `needs_refund` are set and a support ticket is auto-created.
5. Event is marked `PROCESSED` or `FAILED` in `webhook_events`.

---

## Admin — Businesses

All routes require `X-Internal-Key` header.

---

### List all businesses

```
GET /admin/businesses
```

**Response — `200 OK`**

```json
[
  {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "slug": "gameszone-chennai",
    "name": "GamesZone Chennai",
    "description": "Your one-stop shop for PC and console games.",
    "timezone": "Asia/Kolkata",
    "status": "active",
    "settings": {
      "default_currency": "INR",
      "supported_languages": ["en", "ta"]
    }
  }
]
```

---

### Create a business

```
POST /admin/businesses
```

**Request body**

```json
{
  "slug": "gameszone-chennai",
  "name": "GamesZone Chennai",
  "description": "Your one-stop shop for PC and console games.",
  "timezone": "Asia/Kolkata",
  "settings": {}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `slug` | string | Yes | URL-safe unique identifier. Used in webhook routing and admin API paths. |
| `name` | string | Yes | Display name. Injected into the AI's system prompt. |
| `description` | string | No | Optional description. |
| `timezone` | string | No | IANA timezone (default: `Asia/Kolkata`). |
| `settings` | object | No | Flexible per-tenant config (default: `{}`). |

**Response — `201 Created`**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "slug": "gameszone-chennai",
  "name": "GamesZone Chennai",
  "description": "Your one-stop shop for PC and console games.",
  "timezone": "Asia/Kolkata",
  "status": "active",
  "settings": {}
}
```

---

### Get a business

```
GET /admin/businesses/{slug}
```

**Path parameter:** `slug` — the business slug (e.g. `gameszone-chennai`)

**Response — `200 OK`**

Same structure as a single object from the list response.

**Error — `404 Not Found`**

```json
{
  "error": {
    "code": "not_found",
    "message": "Business not found",
    "details": { "slug": "unknown-slug" }
  }
}
```

---

### Update a business

Partial update — only fields provided are changed.

```
PATCH /admin/businesses/{slug}
```

**Request body** — all fields optional

```json
{
  "name": "GamesZone Chennai (Updated)",
  "description": "Updated description.",
  "timezone": "Asia/Kolkata",
  "status": "suspended",
  "settings": {
    "default_currency": "INR"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | New display name |
| `description` | string | New description |
| `timezone` | string | IANA timezone |
| `status` | `BusinessStatus` | `active` · `suspended` · `inactive` |
| `settings` | object | Replaces the entire settings object |

**Response — `200 OK`**

Updated business object.

---

## Admin — Products

All routes require `X-Internal-Key` header. `{slug}` is the business slug.

---

### List products

```
GET /admin/{slug}/products
```

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `category` | string | — | Filter by category (exact match) |
| `limit` | integer | `50` | Max results to return |
| `offset` | integer | `0` | Pagination offset |

**Response — `200 OK`**

```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "sku": "GAME-GTA5-PC",
    "name": "GTA 5",
    "description": "Open world action-adventure game for PC.",
    "price": "229.00",
    "currency": "INR",
    "status": "active",
    "category": "Game",
    "attributes": {
      "platform": "PC",
      "edition": "Standard",
      "delivery": "digital"
    }
  }
]
```

---

### Create a product

```
POST /admin/{slug}/products
```

**Request body**

```json
{
  "name": "GTA 5",
  "description": "Open world action-adventure game for PC.",
  "price": "229.00",
  "currency": "INR",
  "sku": "GAME-GTA5-PC",
  "category": "Game",
  "status": "active",
  "attributes": {
    "platform": "PC",
    "edition": "Standard"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Product display name |
| `price` | decimal string | Yes | Price in the given currency. Must be ≥ 0. |
| `currency` | string | No | 3-letter currency code (default: `INR`) |
| `description` | string | No | Product description |
| `sku` | string | No | Internal stock-keeping code. Unique per business. |
| `category` | string | No | Product category (e.g. `Game`, `Apartment`) |
| `status` | `ProductStatus` | No | Default: `active` |
| `attributes` | object | No | Flexible extra fields (default: `{}`) |

**Response — `201 Created`**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "sku": "GAME-GTA5-PC",
  "name": "GTA 5",
  "description": "Open world action-adventure game for PC.",
  "price": "229.00",
  "currency": "INR",
  "status": "active",
  "category": "Game",
  "attributes": {
    "platform": "PC",
    "edition": "Standard"
  }
}
```

---

### Get a product

```
GET /admin/{slug}/products/{product_id}
```

**Response — `200 OK`**

Single product object (same structure as list item).

**Errors**

| Status | Reason |
|--------|--------|
| `404` | Product not found or belongs to another business |
| `422` | `product_id` is not a valid UUID |

---

### Update a product

Partial update — only fields provided are changed.

```
PATCH /admin/{slug}/products/{product_id}
```

**Request body** — all fields optional

```json
{
  "price": "249.00",
  "status": "active",
  "description": "Updated description.",
  "category": "Game",
  "sku": "GAME-GTA5-PC-V2",
  "attributes": {
    "platform": "PC",
    "edition": "Premium"
  }
}
```

**Response — `200 OK`**

Updated product object.

> **Note:** Changing `price` does not affect existing orders. `order_items` snapshot the price at purchase time.

---

### Archive a product

Soft delete — sets status to `archived`. Existing order items referencing this product are preserved (they hold price snapshots).

```
DELETE /admin/{slug}/products/{product_id}
```

**Response — `204 No Content`**

Empty body.

---

## Admin — Support Tickets

All routes require `X-Internal-Key` header. `{slug}` is the business slug.

Tickets are created automatically by the AI agent. Human agents use these endpoints to manage them.

---

### List open tickets

```
GET /admin/{slug}/support
```

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `priority` | `TicketPriority` | — | Filter: `LOW` · `MEDIUM` · `HIGH` · `URGENT` |
| `limit` | integer | `100` | Max results (ordered oldest first) |

**Response — `200 OK`**

```json
[
  {
    "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "reference": "TKT-2608-4M2XQ8",
    "status": "OPEN",
    "priority": "HIGH",
    "reason": "REFUND_REQUEST",
    "summary": "Customer paid for GTA 5 (ORD-2608-7F3K9Q) but wants a refund. Reason: received wrong edition. Needs human review.",
    "assigned_to": null,
    "customer_id": "c3d4e5f6-a7b8-9012-cdef-123456789012"
  }
]
```

---

### Get a ticket

```
GET /admin/{slug}/support/{reference}
```

**Path parameter:** `reference` — ticket reference (e.g. `TKT-2608-4M2XQ8`, case-insensitive)

**Response — `200 OK`**

Single ticket object (same structure as list item).

**Error — `404 Not Found`**

```json
{
  "error": {
    "code": "not_found",
    "message": "Support ticket not found.",
    "details": { "reference": "TKT-0000-XXXXXX" }
  }
}
```

---

### Update a ticket

Assign an agent, change status, or resolve with a resolution note. All fields are optional.

```
PATCH /admin/{slug}/support/{reference}
```

**Request body**

```json
{
  "assigned_to": "agent@gameszone.in",
  "status": "RESOLVED",
  "resolution": "Refund of ₹229 processed to the original payment method. Transaction ID: ref_xyz."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `assigned_to` | string | Agent handle (email or username). Sets status to `IN_PROGRESS` if ticket was `OPEN`. |
| `status` | `TicketStatus` | New status. Use `RESOLVED` to close with a resolution note. |
| `resolution` | string | Resolution text. Only applied when `status` is `RESOLVED`. |

**Behaviour notes:**
- Setting `assigned_to` without `status` moves the ticket from `OPEN` → `IN_PROGRESS` automatically.
- Setting `status: "RESOLVED"` calls `SupportService.resolve()` which stores the `resolution` note in `metadata_`.
- Setting any other `status` directly updates the column.

**Response — `200 OK`**

Updated ticket object.

---

## Admin — Customers

All routes require `X-Internal-Key` header. `{slug}` is the business slug.

Customers are created automatically on first contact. These endpoints are read-only.

---

### List customers

```
GET /admin/{slug}/customers
```

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `50` | Max results |
| `offset` | integer | `0` | Pagination offset |

**Response — `200 OK`**

```json
[
  {
    "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
    "name": "Rajesh Kumar",
    "phone": "+919876543210",
    "email": null
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | Unique customer ID |
| `name` | string or null | Display name from messaging platform |
| `phone` | string or null | E.164 phone number (e.g. `+919876543210`). Null for Telegram-only customers. |
| `email` | string or null | Email. Null unless set via admin. |

---

### Get a customer

```
GET /admin/{slug}/customers/{customer_id}
```

**Response — `200 OK`**

Single customer object (same structure as list item).

**Errors**

| Status | Reason |
|--------|--------|
| `404` | Customer not found or belongs to another business |
| `422` | `customer_id` is not a valid UUID |

---

## Admin — Knowledge Base

All routes require `X-Internal-Key` header. `{slug}` is the business slug.

Knowledge articles are searchable by the AI agent when customers ask support questions. Only `published` articles appear in search results.

---

### List published articles

```
GET /admin/{slug}/knowledge
```

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `50` | Max results (ordered by title) |

**Response — `200 OK`**

```json
[
  {
    "id": "d4e5f6a7-b8c9-0123-defa-234567890123",
    "title": "How to launch the game after purchase",
    "content": "After your payment is confirmed, you will receive an activation key via WhatsApp. Follow these steps to activate...",
    "source": "manual",
    "keywords": ["launch aagala", "open aagala", "game not starting", "activation"],
    "status": "published"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | Article ID |
| `title` | string | Article title. Used in the AI's system prompt as orientation (tells the AI what topics exist). |
| `content` | string | Full article body. Sent to the AI (truncated to 1500 chars to save tokens). |
| `source` | string or null | Origin: `"manual"`, a URL, a doc name. For auditing. |
| `keywords` | string[] | Extra search terms — Tanglish phrases, slang, misspellings. The main tuning knob for search recall. |
| `status` | `KnowledgeStatus` | `draft` · `published` · `archived`. Only `published` articles appear in AI search. |

---

### Create an article

```
POST /admin/{slug}/knowledge
```

**Request body**

```json
{
  "title": "How to launch the game after purchase",
  "content": "After your payment is confirmed, you will receive an activation key via WhatsApp within 30 minutes. To activate your game:\n1. Open the game launcher\n2. Click 'Activate'\n3. Enter the key exactly as sent",
  "source": "manual",
  "keywords": ["launch aagala", "open aagala", "game not starting", "activation", "key"],
  "status": "published"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Article title |
| `content` | string | Yes | Full article body |
| `source` | string | No | Where this came from |
| `keywords` | string[] | No | Extra search terms (default: `[]`) |
| `status` | `KnowledgeStatus` | No | Default: `published` |

**Response — `201 Created`**

```json
{
  "id": "d4e5f6a7-b8c9-0123-defa-234567890123",
  "title": "How to launch the game after purchase",
  "content": "After your payment is confirmed...",
  "source": "manual",
  "keywords": ["launch aagala", "open aagala", "game not starting", "activation", "key"],
  "status": "published"
}
```

---

### Get an article

```
GET /admin/{slug}/knowledge/{article_id}
```

**Response — `200 OK`**

Single article object (same structure as list item).

**Errors**

| Status | Reason |
|--------|--------|
| `404` | Article not found or belongs to another business |
| `422` | `article_id` is not a valid UUID |

---

### Update an article

Partial update — only fields provided are changed.

```
PATCH /admin/{slug}/knowledge/{article_id}
```

**Request body** — all fields optional

```json
{
  "title": "How to launch your game (updated)",
  "content": "Updated instructions...",
  "keywords": ["launch aagala", "game not opening", "crash", "activation"],
  "status": "published"
}
```

**Response — `200 OK`**

Updated article object.

> **Tip:** Updating `keywords` replaces the entire array. To add a keyword, fetch the article first, append to its `keywords` array, then send the full updated array.

---

### Archive an article

Soft delete — sets status to `archived`. The article is removed from AI search results immediately.

```
DELETE /admin/{slug}/knowledge/{article_id}
```

**Response — `204 No Content`**

Empty body.

---

## Health

```
GET /health
```

Liveness probe. Does **not** hit the database — safe to call at high frequency from a load balancer.

**Response — `200 OK`**

```json
{
  "status": "ok",
  "env": "local"
}
```

| Field | Values |
|-------|--------|
| `status` | Always `"ok"` (non-200 means the process is down) |
| `env` | `local` · `staging` · `production` |

---

## Quick Reference

### Web (test)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/web/chat` | `X-Internal-Key` (open in local) | Drive the agent synchronously; returns the reply + tools used |

### Webhooks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/webhooks/whatsapp` | None | Meta verification handshake |
| `POST` | `/webhooks/whatsapp` | `X-Hub-Signature-256` | Inbound WhatsApp messages |
| `POST` | `/webhooks/telegram` | `X-Telegram-Bot-Api-Secret-Token` | Inbound Telegram updates |
| `POST` | `/webhooks/razorpay` | `X-Razorpay-Signature` | Payment confirmation events |

### Admin — Businesses

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/businesses` | List all businesses |
| `POST` | `/admin/businesses` | Create a business |
| `GET` | `/admin/businesses/{slug}` | Get a business |
| `PATCH` | `/admin/businesses/{slug}` | Update a business |

### Admin — Products

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/products` | List products |
| `POST` | `/admin/{slug}/products` | Create a product |
| `GET` | `/admin/{slug}/products/{id}` | Get a product |
| `PATCH` | `/admin/{slug}/products/{id}` | Update a product |
| `DELETE` | `/admin/{slug}/products/{id}` | Archive a product |

### Admin — Support Tickets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/support` | List open tickets |
| `GET` | `/admin/{slug}/support/{ref}` | Get a ticket |
| `PATCH` | `/admin/{slug}/support/{ref}` | Assign / resolve a ticket |

### Admin — Customers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/customers` | List customers |
| `GET` | `/admin/{slug}/customers/{id}` | Get a customer |

### Admin — Knowledge Base

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/{slug}/knowledge` | List published articles |
| `POST` | `/admin/{slug}/knowledge` | Create an article |
| `GET` | `/admin/{slug}/knowledge/{id}` | Get an article |
| `PATCH` | `/admin/{slug}/knowledge/{id}` | Update an article |
| `DELETE` | `/admin/{slug}/knowledge/{id}` | Archive an article |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
