"""Vercel Python serverless entry point."""


def _build_app():
    try:
        from app.main import app  # noqa: F401
        return app
    except Exception as exc:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse

        _err = repr(exc)
        fallback = FastAPI()

        @fallback.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        )
        async def _error(request: Request) -> JSONResponse:
            return JSONResponse({"startup_error": _err}, status_code=500)

        return fallback


app = _build_app()
