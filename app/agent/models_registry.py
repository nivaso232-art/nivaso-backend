"""Central registry of AI models available for per-business selection.

Add new models here; the admin API and frontend picker both read from this list.
Validation in business settings also uses ``is_valid_model``.
"""

from __future__ import annotations

from typing import Literal, TypedDict

ModelTier = Literal["powerful", "balanced", "fast"]


class ModelInfo(TypedDict):
    provider: str
    model: str
    label: str
    tier: ModelTier


AVAILABLE_MODELS: list[ModelInfo] = [
    # ── Anthropic — Claude 4 family ──────────────────────────────────────────
    {
        "provider": "anthropic",
        "model": "claude-opus-4-7",
        "label": "Claude Opus 4",
        "tier": "powerful",
    },
    {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4",
        "tier": "balanced",
    },
    {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4",
        "tier": "fast",
    },
    # ── Google — Gemini family ────────────────────────────────────────────────
    {
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "tier": "powerful",
    },
    {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "tier": "balanced",
    },
    {
        "provider": "gemini",
        "model": "gemini-2.0-flash-lite",
        "label": "Gemini 2.0 Flash Lite",
        "tier": "fast",
    },
]

_VALID: frozenset[tuple[str, str]] = frozenset(
    (m["provider"], m["model"]) for m in AVAILABLE_MODELS
)


def is_valid_model(provider: str, model: str) -> bool:
    """Return True if (provider, model) is a recognised combination."""
    return (provider, model) in _VALID
