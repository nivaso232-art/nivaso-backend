"""Application-level encryption for credential secrets at rest.

The credential vault (``product_credentials``) is this app's "secrets manager"
in the sense rule 10 means: an isolated table, not widely joined, whose secret
column is **encrypted** with a key that lives only in the environment
(``CREDENTIAL_ENC_KEY``), never in the database. A dump of the table without the
key yields ciphertext, not passwords.

Uses Fernet (AES-128-CBC + HMAC). Generate a key with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.errors import AppError


class EncryptionError(AppError):
    status_code = 500
    code = "encryption_error"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.credential_enc_key
    if not key:
        raise EncryptionError(
            "CREDENTIAL_ENC_KEY is not set — cannot encrypt/decrypt credentials. "
            "Generate one with Fernet.generate_key()."
        )
    try:
        return Fernet(key.encode())
    except Exception as exc:  # invalid key format
        raise EncryptionError(f"CREDENTIAL_ENC_KEY is invalid: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns a URL-safe token string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a stored token back to plaintext."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionError(
            "Failed to decrypt a credential — wrong CREDENTIAL_ENC_KEY or corrupted data."
        ) from exc
