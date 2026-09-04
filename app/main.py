"""FastAPI application entry point.

Start the server:
    uvicorn app.main:app --reload          # local dev
    uvicorn app.main:app --host 0.0.0.0   # staging / prod (behind a proxy)

The lifespan handler wires logging on startup and closes database connections
on shutdown. Both are cheap enough to do synchronously inside the event loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, mock_payments, web
from app.api.admin import (
    agent_runs,
    businesses,
    channels,
    credentials,
    customers,
    dashboard,
    fulfillments,
    knowledge,
    metrics,
    model_registry,
    orders,
    products,
    support,
    webhook_events,
)
from app.api.super_admin import audit_log as super_audit_log
from app.api.super_admin import businesses as super_businesses
from app.api.super_admin import chat as super_chat
from app.api.super_admin import feature_requests as super_feature_requests
from app.api.super_admin import plans as super_plans
from app.api.deps import require_internal_key, require_super_admin_key, require_admin_auth, require_super_admin_auth
from app.api.webhooks import razorpay, telegram, whatsapp
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info(
        "nivaso_starting",
        env=settings.app_env,
        model=settings.agent_model,
    )
    yield
    await dispose_engine()
    log.info("nivaso_stopped")


app = FastAPI(
    title="Nivaso",
    description="AI-powered e-commerce and support platform.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_local else None,
    redoc_url="/redoc" if settings.is_local else None,
)

register_exception_handlers(app)


@app.middleware("http")
async def add_www_authenticate_on_401(request: Request, call_next: Any) -> Response:
    """Add WWW-Authenticate: Bearer to every 401 response on non-webhook routes.

    Exception handlers cannot reliably inject headers through the CORS middleware
    stack in all FastAPI/Starlette versions, so we do it here as a response
    middleware instead — which runs after the full response is assembled.
    Webhook paths use HMAC auth, not Bearer, so they are excluded.
    """
    response: Response = await call_next(request)
    if response.status_code == 401 and not request.url.path.startswith("/webhooks"):
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


# -- CORS --------------------------------------------------------------------
# Resolution order:
#   1. Local dev  → always allow localhost ports (allow_credentials=True)
#   2. CORS_ORIGINS set → allow those specific origins (allow_credentials=True)
#   3. Production fallback → allow all origins (allow_credentials=False)
#      Safe because every sensitive endpoint requires a valid JWT; the
#      browser same-origin policy only stops credential-free reads.
if settings.is_local:
    _cors_origins = settings.allowed_origins
    _cors_credentials = True
elif settings.cors_origins:
    _cors_origins = settings.allowed_origins
    _cors_credentials = True
else:
    _cors_origins = ["*"]         # permissive fallback; JWT still validates all requests
    _cors_credentials = False     # required by spec when origin is "*"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Internal-Key", "X-Super-Admin-Key"],
)


# -- Webhook routes (public, use their own signature verification) ------------
app.include_router(whatsapp.router)
app.include_router(telegram.router)
app.include_router(razorpay.router)

# -- Mock payment page (public; self-guards on PAYMENTS_MOCK) -----------------
app.include_router(mock_payments.router)

# -- Auth routes (public — no auth required) ----------------------------------
app.include_router(auth.router)

# -- Admin routes (internal only, require X-Internal-Key) --------------------
_admin_deps = [Depends(require_admin_auth)]

app.include_router(businesses.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(products.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(support.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(customers.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(knowledge.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(credentials.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(orders.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(fulfillments.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(webhook_events.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(agent_runs.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(channels.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(metrics.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(model_registry.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(dashboard.router, prefix="/admin", dependencies=_admin_deps)

# -- Super-admin routes (Nivaso operators only — separate key) ----------------
_super_deps = [Depends(require_super_admin_auth)]
app.include_router(super_businesses.router, prefix="/super-admin", dependencies=_super_deps)
app.include_router(super_chat.router, prefix="/super-admin", dependencies=_super_deps)
app.include_router(super_feature_requests.router, prefix="/super-admin", dependencies=_super_deps)
app.include_router(super_audit_log.router, prefix="/super-admin", dependencies=_super_deps)
app.include_router(super_plans.router, prefix="/super-admin", dependencies=_super_deps)

# -- Web test channel --------------------------------------------------------
# No auth in local so you can curl /web/chat directly while testing; still
# key-protected in staging/prod (it spends tokens and touches tenant data).
_web_deps = [] if settings.is_local else _admin_deps
app.include_router(web.router, dependencies=_web_deps)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Minimal liveness probe. Does not hit the database."""
    return {"status": "ok", "env": settings.app_env}
