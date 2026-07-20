"""Secret encryption at rest (SEC-1).

Fernet symmetric encryption keyed by ``DG_MASTER_KEY`` (from env, never in the DB or
git). Used for registrar/VT/bot credentials. Plaintext secrets are never logged.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class CryptoError(Exception):
    """Raised when decryption fails or the master key is missing/invalid."""


@lru_cache
def _fernet() -> Fernet:
    key = settings.dg_master_key
    if not key:
        raise CryptoError("DG_MASTER_KEY is not set")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"invalid DG_MASTER_KEY: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a secret; returns an opaque token string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt`."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise CryptoError("could not decrypt value") from exc


def mask(secret: str | None, *, show: int = 4) -> str:
    """Return a masked representation for display/logging (SEC-2)."""
    if not secret:
        return ""
    if len(secret) <= show:
        return "*" * len(secret)
    return secret[:show] + "***"
