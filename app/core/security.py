"""Password hashing (argon2) — SEC-5.

Wraps argon2-cffi with sensible defaults. ``verify_password`` also reports whether
the stored hash should be upgraded (parameters changed) so callers can rehash.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2 hash for ``password``."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return True iff ``password`` matches ``password_hash``.

    Never raises on a bad password — returns False for mismatches and malformed
    hashes so callers can treat all failures uniformly.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return True if the hash was made with outdated parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True
