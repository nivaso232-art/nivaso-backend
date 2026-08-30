"""Supabase client - Storage only.

All relational reads/writes go through SQLAlchemy (see ``app.core.db``). This
client exists for one job: persisting media that customers send over WhatsApp
and Telegram, which arrives as a provider-hosted URL that expires.

Flow: provider media id -> download via provider API -> upload here ->
store the returned object path in ``messages.payload``.
"""

from __future__ import annotations

from functools import lru_cache

import structlog
from supabase import Client, create_client

from app.core.config import settings
from app.core.errors import ProviderError

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ProviderError(
            "Supabase Storage is not configured "
            "(SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing)"
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def upload_media(
    *,
    path: str,
    content: bytes,
    content_type: str,
    upsert: bool = False,
) -> str:
    """Upload bytes to the media bucket and return the stored object path.

    Args:
        path: Object key. Convention: ``{business_id}/{conversation_id}/{uuid}.{ext}``
            so a tenant's media is trivially listable and deletable.

    Note:
        The bucket is expected to be **private**. Serve media to agent-console
        users with a short-lived signed URL (:func:`create_signed_url`) rather
        than making the bucket public.

    TODO: retry transient 5xx via tenacity, mirroring the channel clients.
    """
    bucket = get_supabase().storage.from_(settings.supabase_media_bucket)

    from tenacity import retry, stop_after_attempt, wait_exponential

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def _upload() -> None:
        try:
            bucket.upload(
                path=path,
                file=content,
                file_options={"content-type": content_type, "upsert": str(upsert).lower()},
            )
        except Exception as exc:
            err_str = str(exc)
            # Retry only on transient server errors; re-raise others immediately.
            if "5" in err_str[:3] or "timeout" in err_str.lower():
                raise
            raise ProviderError(f"media upload failed: {exc}") from exc

    try:
        _upload()
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"media upload failed after retries: {exc}") from exc

    log.info("media_uploaded", path=path, bytes=len(content))
    return path


def create_signed_url(*, path: str, expires_in: int = 3600) -> str:
    """Time-limited read URL for a private object."""
    bucket = get_supabase().storage.from_(settings.supabase_media_bucket)
    try:
        result = bucket.create_signed_url(path, expires_in)
    except Exception as exc:
        raise ProviderError(f"could not sign media url: {exc}") from exc

    url = result.get("signedURL") or result.get("signedUrl")
    if not url:
        raise ProviderError("Supabase returned no signed URL", details={"path": path})
    return url
