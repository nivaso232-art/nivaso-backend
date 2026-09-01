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
- Match the customer's language and register. Many customers write in Tanglish \
(Tamil written in English letters) or mix Tamil and English - reply the same \
way they wrote to you. Do not switch to formal English if they wrote casually.
- Keep replies short. This is a chat window, not an email - two or three \
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
- The customer asks about a product -> search_products, then quote the price \
it returns.
- The customer clearly confirms they want to buy -> create_order, read back \
the total, then create_payment_link.
- The customer has a problem -> translate their problem into English keywords, \
then search_knowledge. Explain the answer in your own words, in their language.
- You cannot solve it, or they ask for a human, or they want a refund -> \
create_support_ticket. Escalating is a good outcome, not a failure.
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
) -> list[dict[str, Any]]:
    """The cacheable system block.

    Returns a list with a single text block carrying ``cache_control``. Content
    is deterministic for a given business: categories and knowledge titles are
    sorted, so a new product in an existing category does not invalidate the
    cache.

    ``knowledge_titles`` gives the agent cheap orientation about what help
    topics exist, so it knows whether searching is worth doing at all.
    """
    sections = [_CORE_RULES.format(business_name=business.name)]

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
    return (
        f"[ctx: customer={customer.display_name}, "
        f"stage={conversation.current_state}, order={order_part}]"
    )
