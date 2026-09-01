"""FastAPI application entry point.

Start the server:
    uvicorn app.main:app --reload          # local dev
    uvicorn app.main:app --host 0.0.0.0   # staging / prod (behind a proxy)

The lifespan handler wires logging on startup and closes database connections
on shutdown. Both are cheap enough to do synchronously inside the event loop.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import structlog
from fastapi import Depends, FastAPI

from app.api import mock_payments, web
from app.api.admin import businesses, credentials, customers, knowledge, products, support
from app.api.deps import require_internal_key
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

# -- Webhook routes (public, use their own signature verification) ------------
app.include_router(whatsapp.router)
app.include_router(telegram.router)
app.include_router(razorpay.router)

# -- Mock payment page (public; self-guards on PAYMENTS_MOCK) -----------------
app.include_router(mock_payments.router)

# -- Admin routes (internal only, require X-Internal-Key) --------------------
_admin_deps = [Depends(require_internal_key)]

app.include_router(businesses.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(products.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(support.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(customers.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(knowledge.router, prefix="/admin", dependencies=_admin_deps)
app.include_router(credentials.router, prefix="/admin", dependencies=_admin_deps)

# -- Web test channel --------------------------------------------------------
# No auth in local so you can curl /web/chat directly while testing; still
# key-protected in staging/prod (it spends tokens and touches tenant data).
_web_deps = [] if settings.is_local else _admin_deps
app.include_router(web.router, dependencies=_web_deps)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Minimal liveness probe. Does not hit the database."""
    return {"status": "ok", "env": settings.app_env}
