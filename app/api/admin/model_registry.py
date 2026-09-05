"""Admin API — available AI models registry (read-only)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.models_registry import AVAILABLE_MODELS

router = APIRouter(prefix="/models", tags=["admin:models"])


class ModelOut(BaseModel):
    provider: str
    model: str
    label: str
    tier: str


@router.get("", response_model=list[ModelOut])
async def list_available_models() -> list[ModelOut]:
    """Return every AI model the platform supports for per-business configuration."""
    return [ModelOut(**m) for m in AVAILABLE_MODELS]
