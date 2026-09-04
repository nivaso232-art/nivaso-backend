"""Vercel Python serverless entry point."""

try:
    from app.main import app  # noqa: F401  — Vercel picks up `app` automatically
except Exception as _exc:
    # Surface startup failures (missing env vars, bad imports, etc.) as readable
    # JSON instead of an opaque Vercel 500 page. CORS headers are injected by
    # Vercel routing, so the browser devtools Network tab will show the body.
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    _startup_error = repr(_exc)
    _fallback = FastAPI()

    @_fallback.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def _error(request: Request) -> JSONResponse:
        return JSONResponse({"startup_error": _startup_error}, status_code=500)

    app = _fallback
