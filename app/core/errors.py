"""Application error hierarchy and FastAPI exception handlers.

Services raise these; routes never build error responses by hand. Agent tools
catch :class:`AppError` and translate it into a ``tool_result`` with
``is_error: True`` so the model can recover in-conversation.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import settings

log = structlog.get_logger(__name__)


class AppError(Exception):
    """Base class for all expected, business-meaningful failures."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    """The request contradicts current state (e.g. cancelling a PAID order)."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class SignatureError(AuthError):
    """A webhook payload failed HMAC verification."""

    code = "invalid_signature"


class InvalidStateTransition(ConflictError):
    code = "invalid_state_transition"


class TenantMismatchError(AppError):
    """A record was reached with the wrong ``business_id``.

    This is never a normal user error - it means a tenant filter was missed or
    an agent tried to reach across businesses. Surfaced as 404 rather than 403
    so the caller learns nothing about the other tenant's data.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ProviderError(AppError):
    """An upstream provider (Meta, Telegram, Razorpay) failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"


class AgentError(AppError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "agent_error"


class ProviderRateLimitError(AgentError):
    """LLM provider is rate-limited or temporarily overloaded.

    Raised before the generic AgentError wrapper so FallbackAgentRunner can
    detect it via ``exc.__cause__`` and retry on the fallback model.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_rate_limit"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            code=exc.code,
            message=exc.message,
            path=request.url.path,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request payload failed validation.",
                    "details": {"errors": exc.errors()},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        try:
            log.exception("unhandled_error", path=request.url.path)
        except Exception:
            pass
        details: dict[str, Any] = {}
        if settings.is_local:
            details = {"exception": type(exc).__name__, "message": str(exc)}
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "details": details,
                }
            },
        )
