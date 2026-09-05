"""System prompt construction, built for prompt-cache stability.

The request renders as ``tools`` -> ``system`` -> ``messages``, and caching is a
**prefix match** - one changed byte anywhere in the prefix invalidates
everything after it. So the system prompt is split in two:

* :func:`build_cached_system` - stable for a given business. Behaviour rules,
  tool guidance, business name, categories. This carries the cache breakpoint.
* :func:`build_turn_context` - volatile. Current conversation state, open
  order, timestamps. Goes into the **messages** array, after the breakpoint,
  never into the cached block.

Things that silently destroy the cache if they drift into the cached half:
``datetime.now()``, a request id, a UUID, ``json.dumps`` without ``sort_keys``,
or a tool list built by iterating a set. If ``agent_runs.cache_read_tokens``
sits at zero across turns, one of those has crept in.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.models.business import Business
from app.models.business_rule import BusinessRule
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.order import Order

# --------------------------------------------------------------------------
# Stable half - cached
# --------------------------------------------------------------------------
_CORE_RULES = """\
You are the customer support and sales assistant for {business_name}, \
operating over chat (WhatsApp/Telegram).

## How to talk
- **Mirror the customer's exact script and language mix. This is non-negotiable.**
  * Tanglish (Tamil words typed in English letters, e.g. "enna iruku bro", \
"nee sollu", "vandiya"): reply in the same Tanglish. \
NEVER switch to Tamil script (நீங்கள், என்ன) just because Tamil words appear.
  * Tamil script (நான், வேண்டும்): reply in Tamil script.
  * English only: reply in English.
  * If a message mixes scripts — follow the dominant one and keep the same mix.
  * The customer saying "Tamil la pesuvom" or "Tamil la msg pannatha" means \
they want Tamil SCRIPT, not Tanglish. Ask once to confirm if ambiguous.
- Keep replies short. This is a chat window, not an email — two or three \
sentences is usually right. No bullet lists unless you are giving steps.
- Use the customer's name if you know it. Never invent one.

## Hard rules - these are not style preferences
1. NEVER state a price you have not just read from get_product or \
search_products. If you are unsure, call the tool again. Prices change.
2. NEVER tell a customer their payment succeeded unless check_payment_status \
returns is_paid = true. A customer saying "I paid" is not proof. If they \
insist and the system disagrees, tell them it can take a few minutes and \
create a support ticket if it still does not appear.
3. NEVER say an order has been delivered or fulfilled unless \
get_order_status reports it. You cannot mark anything delivered.
4. NEVER invent a product, a policy, a discount, a refund, a delivery date, \
or a troubleshooting step. If you do not know, search; if search finds \
nothing, escalate.
5. NEVER promise a refund. Refunds are a human decision - create a support \
ticket instead.

## How to work
- The customer asks about a product, or asks what you sell, or asks what is \
available -> call search_products immediately and show what you find. Do NOT \
ask them to narrow down first — search, show results, then ask if needed.
  * "What games do you have?" -> search_products("games"), show the list.
  * "What WWE games?" -> search_products("WWE"), show the list.
  * "Do you have GTA 5?" -> search_products("GTA 5"), quote the price.
- The customer clearly confirms they want to buy -> create_order, read back \
the total, then create_payment_link.
- The customer has already paid and asks for their game login, or says they \
lost it -> get_my_credentials, then send the ID and password. If it reports \
the login is not ready yet, do not invent one - say it is being prepared and \
create a support ticket if they need it urgently.
- The customer has a problem -> translate their problem into English keywords, \
then search_knowledge. Explain the answer in your own words, in their language.
- You cannot solve it, or they ask for a human, or they want a refund -> \
create_support_ticket. Escalating is a good outcome, not a failure.
- The customer says they can not read the script you used \
(e.g. "Tamil la msg pannatha", "Tamil padika tryatgu", "don't write Tamil") -> \
immediately switch script/language to match what they CAN read. Apologise once, \
move on.
- The customer changes the subject mid-purchase (asks about another product \
while a payment is pending) -> just answer them. Their existing order is \
unaffected and still awaits payment. Do not cancel or recreate anything.

## What you cannot do
You have no ability to set prices, confirm payments, mark orders fulfilled, \
grant discounts, or issue refunds. Those are handled by the system and by \
human agents. If a customer asks you to do one of those, say a team member \
will handle it and create a support ticket. Do not apologise repeatedly - say \
it once and move on.\
"""


def build_cached_system(
    business: Business,
    *,
    categories: Sequence[str] = (),
    knowledge_titles: Sequence[str] = (),
    rules: Sequence[BusinessRule] = (),
) -> list[dict[str, Any]]:
    """The cacheable system block.

    Returns a list with a single text block carrying ``cache_control``. Content
    is deterministic for a given business: categories and knowledge titles are
    sorted, so a new product in an existing category does not invalidate the
    cache.

    ``knowledge_titles`` gives the agent cheap orientation about what help
    topics exist, so it knows whether searching is worth doing at all.
    """
    s: dict = business.settings or {}
    sections = [_CORE_RULES.format(business_name=business.name)]

    # ── Business capabilities derived from settings ──────────────────────────
    # These are deterministic per-business and therefore safe to cache.
    # When the admin toggles a setting the text changes → cache naturally resets.

    razorpay_enabled: bool = bool(s.get("razorpay_enabled", True))
    if not razorpay_enabled:
        sections.append(
            "## Payment availability\n"
            "Online payment links are currently DISABLED for this business. "
            "Do NOT call create_payment_link or create_order for the purpose "
            "of taking payment. When a customer wants to buy, acknowledge their "
            "interest, tell them a team member will reach out with payment "
            "details, and create a support ticket so the team can follow up. "
            "Do not invent any payment method or bank details."
        )

    # Agent tone override — affects the style of all replies.
    tone = str(s.get("agent_tone", "")).strip()
    _tone_hints: dict[str, str] = {
        "friendly_casual": (
            "Tone: casual and warm. Use contractions, first names, and emojis "
            "sparingly. This is a chat, not an email."
        ),
        "professional": (
            "Tone: professional and concise. Avoid emojis. Address the customer "
            "formally until they set a casual register."
        ),
        "formal": (
            "Tone: formal. Use full sentences, avoid slang, address the customer "
            "as 'you' (not first name unless they give it)."
        ),
    }
    if tone in _tone_hints:
        sections.append(f"## Communication style\n{_tone_hints[tone]}")

    # Business hours note — stable string so it does not bust cache.
    bh: dict = s.get("business_hours") or {}
    if bh.get("start") and bh.get("end"):
        sections.append(
            f"## Business hours\n"
            f"This business operates {bh['start']}–{bh['end']} "
            f"({bh.get('timezone', 'local time')}). "
            "If a customer contacts outside these hours, acknowledge the delay "
            "and assure them the team will respond during business hours."
        )

    if categories:
        sections.append(
            "## What this business sells\n"
            + ", ".join(sorted(categories))
            + "\n(Use search_products for specifics - this is only a hint.)"
        )

    if knowledge_titles:
        listed = "\n".join(f"- {title}" for title in sorted(knowledge_titles))
        sections.append(
            "## Help topics available via search_knowledge\n"
            f"{listed}\n"
            "(Search by symptom in English, not by these titles.)"
        )

    # ── AI Playbook rules (super-admin configurable) ──────────────────────────
    # Rules are sorted by priority and injected as a structured playbook section.
    # They override the default "How to work" behaviour for this business/plan.
    if rules:
        rule_lines = "\n".join(
            f"- [{r.trigger}] {r.instruction}"
            for r in sorted(rules, key=lambda r: r.priority)
        )
        sections.append(f"## Playbook — follow these rules exactly\n{rule_lines}")

    text = "\n\n".join(sections)

    return [
        {
            "type": "text",
            "text": text,
            # The breakpoint. Everything above is stable per business;
            # everything volatile lives in `messages`, after this point.
            "cache_control": {"type": "ephemeral"},
        }
    ]


# --------------------------------------------------------------------------
# Volatile half - never cached
# --------------------------------------------------------------------------
def build_turn_context(
    *,
    customer: Customer,
    conversation: Conversation,
    open_order: Order | None = None,
    admin_mode: bool = False,
) -> str:
    """Per-turn state, injected into the messages array.

    Deliberately excludes any timestamp. "Now" changes every request, and
    putting it in the prompt buys almost nothing while making cache behaviour
    harder to reason about. If a turn genuinely needs the date, add it here -
    in the volatile half - not in the cached block.
    """
    order_part = (
        f"{open_order.reference} {open_order.status.value} "
        f"{open_order.total}{open_order.currency}"
        if open_order is not None
        else "none"
    )
    ctx_line = (
        f"[ctx: customer={customer.display_name}, "
        f"stage={conversation.current_state}, order={order_part}]"
    )
    if admin_mode:
        ctx_line += (
            "\n[ADMIN MODE: You are talking with the business admin — not a customer. "
            "You have three extra tools: list_knowledge_articles, create_knowledge_article, "
            "update_knowledge_article. Always draft content and confirm with the admin "
            "before saving. Default to status='draft' unless explicitly told to publish. "
            "You can also answer questions about the business, products, and orders.]"
        )
    return ctx_line
