"""Application settings.

This is the ONLY place that reads the environment. Everything else imports
`settings` from here - no `os.getenv` anywhere else in the codebase.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentEffort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Application ------------------------------------------------------
    app_env: Literal["local", "staging", "production"] = "local"
    log_level: str = "INFO"
    internal_api_key: str = "change-me"

    # -- Database ---------------------------------------------------------
    # Pooler (port 6543) for the app; direct (port 5432) for Alembic DDL.
    database_url: PostgresDsn
    database_direct_url: PostgresDsn
    db_echo: bool = False

    # -- Supabase (Storage only) ------------------------------------------
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_media_bucket: str = "media"

    # -- Anthropic / agent ------------------------------------------------
    anthropic_api_key: str = ""
    # Required only for identity-linked (Personal) API keys, which must name the
    # workspace each request acts in. Leave blank for a normal Workspace key.
    anthropic_workspace_id: str = ""
    agent_model: str = "claude-sonnet-5"

    # -- LLM provider selection -------------------------------------------
    # "anthropic" (Claude) or "gemini" (Google AI Studio). Selects which
    # agent runner app/agent/factory.py builds.
    llm_provider: Literal["anthropic", "gemini"] = "anthropic"

    # -- Gemini (Google AI Studio) ----------------------------------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"

    # -- Credential vault -------------------------------------------------
    # Fernet key used to encrypt game-account secrets at rest. Generate with
    # Fernet.generate_key(). Without it, credential delivery cannot run.
    credential_enc_key: str = ""
    agent_effort: AgentEffort = "low"
    agent_max_tokens: int = 1024  # 512 was truncating tool-call reasoning mid-stream
    agent_max_iterations: int = 5  # purchase flow rarely needs more than 4 tool hops

    # -- WhatsApp ---------------------------------------------------------
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_graph_api_version: str = "v21.0"

    # -- Telegram ---------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # -- Razorpay ---------------------------------------------------------
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # -- Mock payments ----------------------------------------------------
    # When true, the agent issues a mock payment link (no Razorpay call), and
    # opening that link completes the payment and triggers real delivery. Use
    # while Razorpay is unavailable (e.g. pending KYC). Never enable in prod.
    payments_mock: bool = False
    # Base URL the mock link points at (must be reachable by whoever opens it).
    public_base_url: str = "http://localhost:8000"

    # -- Multi-tenant routing --------------------------------------------
    # For single-business deployments: the slug of the business that all
    # inbound webhook messages are routed to. Multi-tenant routing (mapping
    # phone-number-id or bot-token to a business slug) can be layered on top
    # when needed.
    default_business_slug: str = ""

    @field_validator("database_url", "database_direct_url")
    @classmethod
    def _require_asyncpg_driver(cls, v: PostgresDsn) -> PostgresDsn:
        """Both URLs are consumed by async SQLAlchemy engines.

        A bare ``postgresql://`` URL silently selects the sync psycopg2 driver
        and fails at connect time with a confusing error, so reject it here.
        """
        if v.scheme != "postgresql+asyncpg":
            raise ValueError(
                f"expected scheme 'postgresql+asyncpg', got '{v.scheme}'. "
                "Rewrite the Supabase connection string accordingly."
            )
        return v

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def whatsapp_graph_base_url(self) -> str:
        return f"https://graph.facebook.com/{self.whatsapp_graph_api_version}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment


settings = get_settings()
