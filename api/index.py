"""Vercel Python serverless entry point.

Vercel's @vercel/python builder looks for an `app` variable (ASGI/WSGI) in
this file.  We simply re-export the FastAPI application instance from
app/main.py so the full application runs as a single serverless function.
"""
from app.main import app  # noqa: F401  — Vercel picks up `app` automatically
