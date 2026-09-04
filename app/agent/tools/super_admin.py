"""Super-admin-only tools: platform-wide management operations.

These tools are injected ONLY by SuperAdminAgentRunner, which is reached
exclusively through POST /super-admin/chat (requires X-Super-Admin-Key or
a super_admin JWT).  They are never available in customer or business-admin
chat sessions.

Handlers take ``SuperAdminContext`` as their first argument instead of
``ToolContext``.  Because Python dispatches by position, not annotation,
the ToolSpec machinery routes them correctly — the runner calls
``spec.execute(super_admin_ctx, arguments)`` and each handler receives the
super-admin context as its first positional arg.

Design rules:
  • Reads: use the shared session directly — no UnitOfWork needed.
  • Writes: always wrap in ``async with UnitOfWork(ctx.session):``.
  • Audit: every mutation must call ``_audit(ctx, business_id, action, details)``.
  • Errors: return ``{"error": "..."}`` dicts; never raise inside a handler.
  • No tenant-ID parameters: super-admin identifies businesses by slug, not UUID.
"""

from __future__ import annotations

import json
import secrets
import string
import uuid as _uuid
from typing import Any

import bcrypt

from app.agent.context import SuperAdminContext
from app.agent.tools.base import ToolSpec, enum_prop, integer_prop, schema, string_prop
from app.core.uow import UnitOfWork
from app.entitlements.flags import VALID_PLANS
from app.entitlements.resolver import resolve
from app.models.business import Business
from app.models.enums import BusinessStatus
from app.repositories.business_admins import BusinessAdminRepository
from app.repositories.businesses import BusinessRepository
from app.repositories.entitlements import EntitlementRepository
from app.repositories.feature_requests import FeatureRequestRepository
from app.repositories.plan_definitions import PlanDefinitionRepository


# ── Shared helpers ────────────────────────────────────────────────────────────

_NULL_SENTINELS = frozenset({"null", "none", "undefined", "n/a", ""})


def _str(val: str | None) -> str | None:
    """Normalise optional string filter params from the AI.

    _strip_for_anthropic converts ["string","null"] → "string" so Anthropic
    strict mode cannot send JSON null — it sends the literal string "null"
    instead.  Map all null-sentinel strings to Python None so filter logic
    works correctly.
    """
    if val is None:
        return None
    stripped = val.strip().lower()
    return None if stripped in _NULL_SENTINELS else val.strip()


def _int(val: int | None, default: int) -> int:
    """Return val if it is a positive int, otherwise return default."""
    return val if (val is not None and val > 0) else default


async def _audit(
    ctx: SuperAdminContext,
    business_id: object,
    action: str,
    details: dict[str, Any],
) -> None:
    try:
        from app.repositories.audit_log import AuditLogRepository
        await AuditLogRepository(ctx.session).record(
            business_id=business_id,  # type: ignore[arg-type]
            action=action,
            details=details,
            performed_by=ctx.performed_by,
        )
    except Exception:
        pass


def _ent_tuple(ent: Any) -> tuple[str, dict, str | None]:
    if ent is None:
        return "free", {}, None
    return ent.plan, ent.overrides, ent.granted_by


def _biz_out(biz: Business, plan: str, overrides: dict) -> dict[str, Any]:
    return {
        "slug": biz.slug,
        "name": biz.name,
        "status": biz.status.value,
        "plan": plan,
        "timezone": biz.timezone,
        "resolved_flags": resolve(plan, overrides),
        "overrides": overrides,
        "created_at": biz.created_at.isoformat(),
    }


# ── 1. get_platform_overview ──────────────────────────────────────────────────

async def _get_platform_overview(ctx: SuperAdminContext) -> dict[str, Any]:
    biz_repo = BusinessRepository(ctx.session)
    ent_repo = EntitlementRepository(ctx.session)
    fr_repo = FeatureRequestRepository(ctx.session)

    businesses = list(await biz_repo.list_all())

    try:
        all_ents = {e.business_id: e for e in await ent_repo.list_all()}
    except Exception:
        all_ents = {}

    by_status: dict[str, int] = {}
    by_plan: dict[str, int] = {}
    for biz in businesses:
        by_status[biz.status.value] = by_status.get(biz.status.value, 0) + 1
        plan = all_ents[biz.id].plan if biz.id in all_ents else "unknown"
        by_plan[plan] = by_plan.get(plan, 0) + 1

    try:
        pending_count = len(list(await fr_repo.list_pending()))
    except Exception:
        pending_count = 0

    return {
        "total_businesses": len(businesses),
        "by_status": by_status,
        "by_plan": by_plan,
        "pending_feature_requests": pending_count,
    }


GET_PLATFORM_OVERVIEW = ToolSpec(
    name="get_platform_overview",
    description=(
        "Return a high-level snapshot of the platform: total businesses, breakdown "
        "by plan tier, breakdown by status (active/suspended/deactivated), and the "
        "count of pending feature requests. Call this first when the admin asks for "
        "a summary or doesn't specify what they want."
    ),
    input_schema=schema(properties={}),
    handler=_get_platform_overview,
)


# ── 2. list_businesses ────────────────────────────────────────────────────────

async def _list_businesses(ctx: SuperAdminContext) -> dict[str, Any]:
    biz_repo = BusinessRepository(ctx.session)
    ent_repo = EntitlementRepository(ctx.session)

    businesses = list(await biz_repo.list_all())

    try:
        all_ents = {e.business_id: e for e in await ent_repo.list_all()}
    except Exception:
        all_ents = {}

    rows = []
    for biz in businesses:
        ent = all_ents.get(biz.id)
        biz_plan, _, _ = _ent_tuple(ent)
        rows.append({
            "slug": biz.slug,
            "name": biz.name,
            "status": biz.status.value,
            "plan": biz_plan,
            "created_at": biz.created_at.isoformat()[:10],
        })

    # Group by plan so the caller never needs to invoke this tool more than once.
    by_plan: dict[str, list[dict]] = {}
    for biz in rows:
        by_plan.setdefault(biz["plan"], []).append({
            "slug": biz["slug"],
            "name": biz["name"],
            "status": biz["status"],
        })

    return {
        "count": len(rows),
        "by_plan": by_plan,
        "businesses": rows,
    }


LIST_BUSINESSES = ToolSpec(
    name="list_businesses",
    description=(
        "Return ALL businesses on the platform — no filters, no parameters. "
        "The response includes every business plus a by_plan map so you can answer "
        "plan-grouping questions in a single call. Call this tool exactly once."
    ),
    input_schema=schema(properties={}),
    handler=_list_businesses,
)


# ── 3. get_business ───────────────────────────────────────────────────────────

async def _get_business(ctx: SuperAdminContext, slug: str) -> dict[str, Any]:
    biz_repo = BusinessRepository(ctx.session)
    ent_repo = EntitlementRepository(ctx.session)

    try:
        biz = await biz_repo.get_by_slug_or_raise(slug)
    except Exception:
        return {"error": f"Business '{slug}' not found."}

    try:
        ent = await ent_repo.get(biz.id)
    except Exception:
        ent = None

    plan, overrides, granted_by = _ent_tuple(ent)
    return {**_biz_out(biz, plan, overrides), "granted_by": granted_by}


GET_BUSINESS = ToolSpec(
    name="get_business",
    description=(
        "Get full details for one business by its slug: name, status, plan, "
        "per-business flag overrides, and all resolved feature flags."
    ),
    input_schema=schema(
        properties={"slug": string_prop("The business slug, e.g. 'nivaso-gaming'.")}
    ),
    handler=_get_business,
)


# ── 4. create_business ────────────────────────────────────────────────────────

async def _create_business(
    ctx: SuperAdminContext,
    slug: str,
    name: str,
    plan: str | None = None,
    timezone: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    if plan and plan not in VALID_PLANS:
        return {"error": f"Unknown plan '{plan}'. Valid: {sorted(VALID_PLANS)}"}

    chosen_plan = plan or "free"
    biz_repo = BusinessRepository(ctx.session)

    existing = await biz_repo.get_by_slug(slug)
    if existing is not None:
        return {"error": f"A business with slug '{slug}' already exists."}

    alphabet = string.ascii_letters + string.digits
    plain_password = "".join(secrets.choice(alphabet) for _ in range(12))
    password_hash = bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()

    biz = Business(
        slug=slug,
        name=name,
        description=description,
        timezone=timezone or "Asia/Kolkata",
        status=BusinessStatus.ACTIVE,
        settings={},
    )

    ent_repo = EntitlementRepository(ctx.session)
    admin_repo = BusinessAdminRepository(ctx.session)

    try:
        async with UnitOfWork(ctx.session):
            await biz_repo.add(biz)
            ent = await ent_repo.set_plan(biz.id, chosen_plan, granted_by=ctx.performed_by)
            await admin_repo.create(
                business_id=biz.id,
                username=slug,
                password_hash=password_hash,
            )
            await _audit(ctx, biz.id, "business_created", {"plan": chosen_plan})
        plan_out, overrides_out, _ = ent.plan, ent.overrides, ent.granted_by
    except Exception as exc:
        return {"error": f"Failed to create business: {exc}"}

    return {
        "slug": biz.slug,
        "name": biz.name,
        "plan": plan_out,
        "status": "active",
        "admin_username": slug,
        "admin_password": plain_password,
        "note": "Save the admin_password now — it is shown only once.",
    }


CREATE_BUSINESS = ToolSpec(
    name="create_business",
    description=(
        "Create a new client business. Assigns the given plan tier and generates "
        "admin login credentials. Returns the admin_password in plaintext — it is "
        "shown only once. Always include it verbatim in your reply so the super-admin "
        "can save it."
    ),
    input_schema=schema(
        properties={
            "slug":        string_prop("Unique URL-safe identifier, e.g. 'acme-store'. Lowercase letters, digits, hyphens only."),
            "name":        string_prop("Display name of the business."),
            "plan":        string_prop("Plan tier: free | starter | pro | enterprise. Defaults to 'free'.", nullable=True),
            "timezone":    string_prop("IANA timezone, e.g. 'Asia/Kolkata'. Defaults to 'Asia/Kolkata'.", nullable=True),
            "description": string_prop("Optional short description.", nullable=True),
        }
    ),
    handler=_create_business,
)


# ── 5. change_business_plan ───────────────────────────────────────────────────

async def _change_business_plan(
    ctx: SuperAdminContext,
    slug: str,
    plan: str,
) -> dict[str, Any]:
    if plan not in VALID_PLANS:
        return {"error": f"Unknown plan '{plan}'. Valid: {sorted(VALID_PLANS)}"}

    biz_repo = BusinessRepository(ctx.session)
    try:
        biz = await biz_repo.get_by_slug_or_raise(slug)
    except Exception:
        return {"error": f"Business '{slug}' not found."}

    ent_repo = EntitlementRepository(ctx.session)
    try:
        async with UnitOfWork(ctx.session):
            ent = await ent_repo.set_plan(biz.id, plan, granted_by=ctx.performed_by)
            await _audit(ctx, biz.id, "plan_changed", {"plan": plan})
    except Exception as exc:
        return {"error": f"Failed to change plan: {exc}"}

    return {
        "slug": slug,
        "plan": ent.plan,
        "message": f"Plan for '{slug}' changed to '{plan}'.",
    }


CHANGE_BUSINESS_PLAN = ToolSpec(
    name="change_business_plan",
    description=(
        "Assign a new plan tier to a business. Takes effect immediately — the business "
        "gains or loses tool access and feature flags at once. The change is audit-logged."
    ),
    input_schema=schema(
        properties={
            "slug": string_prop("Business slug."),
            "plan": enum_prop("New plan tier.", ["free", "starter", "pro", "enterprise"]),
        }
    ),
    handler=_change_business_plan,
)


# ── 6. change_business_status ─────────────────────────────────────────────────

async def _change_business_status(
    ctx: SuperAdminContext,
    slug: str,
    status: str,
) -> dict[str, Any]:
    valid_statuses = {s.value for s in BusinessStatus}
    if status not in valid_statuses:
        return {"error": f"Unknown status '{status}'. Valid: {sorted(valid_statuses)}"}

    biz_repo = BusinessRepository(ctx.session)
    try:
        biz = await biz_repo.get_by_slug_or_raise(slug)
    except Exception:
        return {"error": f"Business '{slug}' not found."}

    try:
        async with UnitOfWork(ctx.session):
            biz.status = BusinessStatus(status)
            await _audit(ctx, biz.id, "status_changed", {"status": status})
    except Exception as exc:
        return {"error": f"Failed to update status: {exc}"}

    return {
        "slug": slug,
        "status": status,
        "message": f"Business '{slug}' is now {status}.",
    }


CHANGE_BUSINESS_STATUS = ToolSpec(
    name="change_business_status",
    description=(
        "Suspend, reactivate, or deactivate a business. Suspending immediately blocks "
        "all webhook traffic and agent responses for that tenant. 'deactivated' is "
        "permanent — prefer 'suspended' for temporary blocks. Change is audit-logged."
    ),
    input_schema=schema(
        properties={
            "slug":   string_prop("Business slug."),
            "status": enum_prop("New status.", ["active", "suspended", "deactivated"]),
        }
    ),
    handler=_change_business_status,
)


# ── 7. set_feature_override ───────────────────────────────────────────────────

async def _set_feature_override(
    ctx: SuperAdminContext,
    slug: str,
    flag: str,
    value_json: str,
) -> dict[str, Any]:
    try:
        value = json.loads(value_json)
    except Exception:
        return {"error": f"value_json is not valid JSON: {value_json!r}"}

    biz_repo = BusinessRepository(ctx.session)
    try:
        biz = await biz_repo.get_by_slug_or_raise(slug)
    except Exception:
        return {"error": f"Business '{slug}' not found."}

    ent_repo = EntitlementRepository(ctx.session)
    try:
        ent = await ent_repo.get(biz.id)
        current_overrides = dict(ent.overrides) if ent else {}
        new_overrides = {**current_overrides, flag: value}

        async with UnitOfWork(ctx.session):
            await ent_repo.set_overrides(biz.id, new_overrides, granted_by=ctx.performed_by)
            await _audit(ctx, biz.id, "override_set", {"flag": flag, "value": value})
    except Exception as exc:
        return {"error": f"Failed to set override: {exc}"}

    return {
        "slug": slug,
        "flag": flag,
        "value": value,
        "message": f"Override '{flag}' = {value} applied to '{slug}'.",
    }


SET_FEATURE_OVERRIDE = ToolSpec(
    name="set_feature_override",
    description=(
        "Enable or disable a single feature flag for one business on top of their "
        "plan defaults. This is additive — other overrides are preserved. "
        "Use value_json='true' to enable, 'false' to disable, 'null' to remove the "
        "override, or a JSON array like '[\"claude-sonnet-4-6\"]' for list flags. "
        "Change is audit-logged."
    ),
    input_schema=schema(
        properties={
            "slug":       string_prop("Business slug."),
            "flag":       string_prop("Dotted flag key, e.g. 'channel.whatsapp' or 'orders.enabled'."),
            "value_json": string_prop(
                "JSON-encoded value: 'true', 'false', 'null', a number like '5', "
                "or a JSON array like '[\"claude-haiku-4-5-20251001\"]'."
            ),
        }
    ),
    handler=_set_feature_override,
)


# ── 8. remove_feature_override ───────────────────────────────────────────────

async def _remove_feature_override(
    ctx: SuperAdminContext,
    slug: str,
    flag: str,
) -> dict[str, Any]:
    biz_repo = BusinessRepository(ctx.session)
    try:
        biz = await biz_repo.get_by_slug_or_raise(slug)
    except Exception:
        return {"error": f"Business '{slug}' not found."}

    ent_repo = EntitlementRepository(ctx.session)
    try:
        ent = await ent_repo.get(biz.id)
        current_overrides = dict(ent.overrides) if ent else {}
        if flag not in current_overrides:
            return {"slug": slug, "flag": flag, "message": f"No override for '{flag}' found — nothing to remove."}

        new_overrides = {k: v for k, v in current_overrides.items() if k != flag}
        async with UnitOfWork(ctx.session):
            await ent_repo.set_overrides(biz.id, new_overrides, granted_by=ctx.performed_by)
            await _audit(ctx, biz.id, "override_removed", {"flag": flag})
    except Exception as exc:
        return {"error": f"Failed to remove override: {exc}"}

    return {
        "slug": slug,
        "flag": flag,
        "message": f"Override '{flag}' removed from '{slug}'. Flag now follows plan default.",
    }


REMOVE_FEATURE_OVERRIDE = ToolSpec(
    name="remove_feature_override",
    description=(
        "Remove a per-business flag override, reverting that flag to the plan-tier "
        "default. Use this to undo a set_feature_override without changing anything "
        "else. Change is audit-logged."
    ),
    input_schema=schema(
        properties={
            "slug": string_prop("Business slug."),
            "flag": string_prop("Dotted flag key to remove, e.g. 'channel.whatsapp'."),
        }
    ),
    handler=_remove_feature_override,
)


# ── 9. list_feature_requests ──────────────────────────────────────────────────

async def _list_feature_requests(
    ctx: SuperAdminContext,
    status: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    status = _str(status)
    cap = min(_int(limit, 30), 100)

    fr_repo = FeatureRequestRepository(ctx.session)
    biz_repo = BusinessRepository(ctx.session)

    try:
        if status:
            requests = list(await fr_repo.list_all(status=status))
        else:
            requests = list(await fr_repo.list_pending())
    except Exception:
        return {"count": 0, "requests": [], "note": "Feature requests table may not exist yet."}

    businesses = {b.id: b for b in await biz_repo.list_all()}
    rows = []
    for r in requests[:cap]:
        biz = businesses.get(r.business_id)
        rows.append({
            "id": str(r.id),
            "business_slug": biz.slug if biz else str(r.business_id),
            "feature": r.feature,
            "reason": r.reason,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        })

    return {
        "count": len(rows),
        "requests": rows,
        "note": "Use request 'id' values when calling review_feature_request.",
    }


LIST_FEATURE_REQUESTS = ToolSpec(
    name="list_feature_requests",
    description=(
        "List feature access requests submitted by businesses. Defaults to 'pending' "
        "only. Pass status='approved' or status='denied' to see reviewed ones. "
        "Returns the request ID needed to call review_feature_request."
    ),
    input_schema=schema(
        properties={
            "status": string_prop(
                "Filter by status: 'pending' (default), 'approved', or 'denied'. "
                "Pass null to get pending only.",
                nullable=True,
            ),
            "limit": integer_prop("Max results (1–100, default 30).", minimum=1, maximum=100, nullable=True),
        }
    ),
    handler=_list_feature_requests,
)


# ── 9. review_feature_request ─────────────────────────────────────────────────

async def _review_feature_request(
    ctx: SuperAdminContext,
    request_id: str,
    decision: str,
    notes: str | None = None,
) -> dict[str, Any]:
    if decision not in ("approved", "denied"):
        return {"error": "decision must be 'approved' or 'denied'."}

    try:
        rid = _uuid.UUID(request_id)
    except ValueError:
        return {"error": f"'{request_id}' is not a valid request ID."}

    fr_repo = FeatureRequestRepository(ctx.session)
    req = await fr_repo.get(rid)
    if req is None:
        return {"error": "Feature request not found."}
    if req.status != "pending":
        return {"error": f"Only pending requests can be reviewed. This one is '{req.status}'."}

    ent_repo = EntitlementRepository(ctx.session)
    try:
        async with UnitOfWork(ctx.session):
            reviewed = await fr_repo.review(
                rid,
                status=decision,
                reviewed_by=ctx.performed_by,
                notes=notes,
            )
            if decision == "approved":
                ent = await ent_repo.get_or_create(req.business_id)
                new_overrides = {**ent.overrides, req.feature: True}
                await ent_repo.set_overrides(req.business_id, new_overrides, granted_by=ctx.performed_by)
            await _audit(
                ctx,
                req.business_id,
                f"request_{decision}",
                {"feature": req.feature, "notes": notes},
            )
    except Exception as exc:
        return {"error": f"Failed to review request: {exc}"}

    return {
        "request_id": str(reviewed.id),
        "feature": reviewed.feature,
        "decision": decision,
        "message": (
            f"Request {decision}. Feature '{reviewed.feature}' has been "
            + ("enabled for the business immediately." if decision == "approved" else "denied.")
        ),
    }


REVIEW_FEATURE_REQUEST = ToolSpec(
    name="review_feature_request",
    description=(
        "Approve or deny a pending feature request. Approving immediately enables "
        "the requested feature for that business via an entitlement override. "
        "Denying leaves the business on its current plan. Both actions are audit-logged."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "request_id": {
                "type": "string",
                "description": "UUID from list_feature_requests.",
            },
            "decision": {
                "type": "string",
                "enum": ["approved", "denied"],
                "description": "Approve or deny the request.",
            },
            "notes": {
                "type": ["string", "null"],
                "description": "Optional explanation shown in the audit log.",
            },
        },
        "required": ["request_id", "decision", "notes"],
        "additionalProperties": False,
    },
    handler=_review_feature_request,
)


# ── 10. get_audit_log ─────────────────────────────────────────────────────────

async def _get_audit_log(
    ctx: SuperAdminContext,
    slug: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    slug = _str(slug)
    biz_repo = BusinessRepository(ctx.session)
    cap = min(_int(limit, 20), 50)

    try:
        from app.repositories.audit_log import AuditLogRepository
        repo = AuditLogRepository(ctx.session)

        if slug:
            try:
                biz = await biz_repo.get_by_slug_or_raise(slug)
            except Exception:
                return {"error": f"Business '{slug}' not found."}
            entries = list(await repo.list_for_business(biz.id, limit=cap))
            businesses = {biz.id: biz}
        else:
            entries = list(await repo.list_all(limit=cap))
            businesses = {b.id: b for b in await biz_repo.list_all()}

    except Exception:
        return {"count": 0, "entries": [], "note": "Audit log table may not exist yet."}

    rows = [
        {
            "business_slug": businesses[e.business_id].slug if e.business_id in businesses else str(e.business_id),
            "action": e.action,
            "details": e.details,
            "performed_by": e.performed_by,
            "created_at": e.created_at.isoformat(),
        }
        for e in entries
    ]
    return {"count": len(rows), "entries": rows}


GET_AUDIT_LOG = ToolSpec(
    name="get_audit_log",
    description=(
        "View recent entitlement audit entries: plan changes, override updates, "
        "status changes, feature request decisions. Pass slug to scope to one "
        "business, or omit to see platform-wide recent activity."
    ),
    input_schema=schema(
        properties={
            "slug":  string_prop("Business slug to scope the log, or null for all businesses.", nullable=True),
            "limit": integer_prop("Number of entries to return (1–50, default 20).", minimum=1, maximum=50, nullable=True),
        }
    ),
    handler=_get_audit_log,
)


# ── 11. update_plan_definition ────────────────────────────────────────────────

async def _update_plan_definition(
    ctx: SuperAdminContext,
    plan_name: str,
    flags_json: str,
) -> dict[str, Any]:
    if plan_name not in VALID_PLANS:
        return {"error": f"Unknown plan '{plan_name}'. Valid: {sorted(VALID_PLANS)}"}

    try:
        incoming_flags: dict = json.loads(flags_json)
    except Exception:
        return {"error": f"flags_json is not valid JSON: {flags_json!r}"}

    if not isinstance(incoming_flags, dict):
        return {"error": "flags_json must be a JSON object (dict)."}

    repo = PlanDefinitionRepository(ctx.session)
    try:
        existing = await repo.get(plan_name)
        merged = dict(existing.flags) if existing else {}
        merged.update(incoming_flags)

        async with UnitOfWork(ctx.session):
            plan_def = await repo.upsert(plan_name, merged, updated_by=ctx.performed_by)
            await ctx.session.refresh(plan_def)
    except Exception as exc:
        return {"error": f"Failed to update plan definition: {exc}"}

    return {
        "plan_name": plan_def.plan_name,
        "updated_at": plan_def.updated_at.isoformat(),
        "flags_changed": list(incoming_flags.keys()),
        "message": (
            f"Plan '{plan_name}' updated. Flags changed: {list(incoming_flags.keys())}. "
            "Affects all businesses on this plan that have no per-business override for the changed flags."
        ),
    }


UPDATE_PLAN_DEFINITION = ToolSpec(
    name="update_plan_definition",
    description=(
        "Update the default feature flags for an entire plan tier. Pass only the "
        "flags that should change — other flags are preserved. This affects every "
        "business on the plan that has no per-business override for that flag. "
        "Use with care: it changes the platform-wide plan definition. "
        "flags_json must be a JSON object e.g. '{\"ai.max_iterations\": 10}'."
    ),
    input_schema=schema(
        properties={
            "plan_name":  enum_prop("Plan tier to update.", ["free", "starter", "pro", "enterprise"]),
            "flags_json": string_prop(
                "JSON object of flag key/value pairs to merge into the plan definition. "
                "Only the keys you include are changed; others are untouched. "
                "Example: '{\"ai.max_iterations\": 10, \"channel.whatsapp\": true}'"
            ),
        }
    ),
    handler=_update_plan_definition,
)


# ── Registry ──────────────────────────────────────────────────────────────────

SUPER_ADMIN_TOOLS: tuple[ToolSpec, ...] = (
    GET_PLATFORM_OVERVIEW,
    LIST_BUSINESSES,
    GET_BUSINESS,
    CREATE_BUSINESS,
    CHANGE_BUSINESS_PLAN,
    CHANGE_BUSINESS_STATUS,
    SET_FEATURE_OVERRIDE,
    REMOVE_FEATURE_OVERRIDE,
    LIST_FEATURE_REQUESTS,
    REVIEW_FEATURE_REQUEST,
    GET_AUDIT_LOG,
    UPDATE_PLAN_DEFINITION,
)
