"""Platform tools: feature-access requests and status checks.

These tools are available to EVERY plan with no feature-flag dependency.
They exist precisely to bridge the gap when a customer needs a capability
the business hasn't enabled yet — the AI can surface that need automatically
instead of dead-ending the conversation.

Flow:
  1. Customer asks for something (e.g. "I want to pay for my order")
  2. AI has no payment tool → tells customer it can't do that right now
  3. AI calls request_feature_access("channel.payments", customer's words)
  4. FeatureRequest row created → business admin sees it in Feature Requests page
  5. On a follow-up visit, customer can ask status → check_feature_request_status

Capability → flag-key mapping (guide the model via description):
  "order processing / place an order"    → orders.enabled
  "payment / pay for order"              → channel.payments
  "WhatsApp"                             → channel.whatsapp
  "Telegram"                             → channel.telegram
  "credential delivery / game accounts"  → credentials.enabled
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agent.context import ToolContext
from app.agent.tools.base import ToolSpec, schema, string_prop
from app.repositories.feature_requests import FeatureRequestRepository


async def request_feature_access(
    ctx: ToolContext,
    feature: str,
    customer_request: str,
) -> dict[str, Any]:
    """Submit a feature-access request for this business on behalf of a customer need."""
    repo = FeatureRequestRepository(ctx.session)

    # Avoid duplicate pending requests for the same feature.
    existing = await repo.list_for_business(ctx.business_id)
    for req in existing:
        if req.status == "pending" and req.feature.lower() == feature.lower():
            return {
                "already_requested": True,
                "status": "pending",
                "instruction": (
                    "Tell the customer their account team has already been notified "
                    "about this and it is under review. Give them ticket reference "
                    f"{req.id} to quote if they follow up."
                ),
            }

    req = await repo.create(
        business_id=ctx.business_id,
        feature=feature,
        reason=f"Customer request: {customer_request}",
    )
    await ctx.session.flush()

    return {
        "submitted": True,
        "request_reference": str(req.id),
        "instruction": (
            "Tell the customer you've flagged this to their account team and they "
            "will be in touch once it's reviewed. Give them reference "
            f"{req.id} if they want to follow up."
        ),
    }


async def check_feature_request_status(
    ctx: ToolContext,
    feature: str | None = None,
) -> dict[str, Any]:
    """Check whether previous feature-access requests have been reviewed."""
    repo = FeatureRequestRepository(ctx.session)
    all_requests = await repo.list_for_business(ctx.business_id)

    if feature:
        all_requests = [r for r in all_requests if feature.lower() in r.feature.lower()]

    if not all_requests:
        return {
            "count": 0,
            "requests": [],
            "instruction": "No feature requests found. Tell the customer there is no pending request for this.",
        }

    return {
        "count": len(all_requests),
        "requests": [
            {
                "reference": str(r.id),
                "feature": r.feature,
                "status": r.status,
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in all_requests
        ],
        "instruction": (
            "Report the status to the customer. "
            "'pending' = under review; 'approved' = the feature is now enabled; "
            "'denied' = the team decided not to enable it (share notes if any)."
        ),
    }


REQUEST_FEATURE_ACCESS = ToolSpec(
    name="request_feature_access",
    description=(
        "Submit a feature-access request when you cannot help a customer because "
        "a capability is not enabled on their business plan. "
        "Use this immediately after explaining that you can't do something — do NOT "
        "leave the customer without a next step. "
        "Pass the feature flag key where possible: "
        "'orders.enabled' for order/purchase requests, "
        "'channel.payments' for payment links, "
        "'channel.whatsapp' or 'channel.telegram' for channel access, "
        "'credentials.enabled' for digital delivery or game account requests. "
        "If unsure of the flag, pass a short description of the capability."
    ),
    input_schema=schema(
        properties={
            "feature": string_prop(
                "The feature flag key or short capability name, e.g. 'orders.enabled' "
                "or 'digital credential delivery'. Used to track the request."
            ),
            "customer_request": string_prop(
                "What the customer was trying to do, in their own words. "
                "Stored as context for the account team reviewing the request."
            ),
        }
    ),
    handler=request_feature_access,
)

CHECK_FEATURE_REQUEST_STATUS = ToolSpec(
    name="check_feature_request_status",
    description=(
        "Check whether a previously submitted feature-access request has been "
        "reviewed and whether the feature has been enabled. "
        "Use when a customer asks 'did anything change?', 'was my request approved?', "
        "or 'can I pay now?' after a previous request was flagged."
    ),
    input_schema=schema(
        properties={
            "feature": string_prop(
                "The capability to check, e.g. 'orders.enabled' or 'payments'. "
                "Pass null to return all requests for this business.",
                nullable=True,
            ),
        }
    ),
    handler=check_feature_request_status,
)
