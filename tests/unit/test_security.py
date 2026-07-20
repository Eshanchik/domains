"""Password hashing (argon2)."""

from __future__ import annotations

from app.core.security import hash_password, needs_rehash, verify_password


def test_hash_and_verify_roundtrip() -> None:
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"  # not plaintext
    assert h.startswith("$argon2")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_rejects_wrong_password() -> None:
    h = hash_password("s3cret-pass")
    assert verify_password("wrong-pass", h) is False


def test_verify_handles_malformed_hash() -> None:
    # Must not raise on a garbage hash — returns False.
    assert verify_password("whatever", "not-a-hash") is False


def test_needs_rehash_false_for_fresh_hash() -> None:
    assert needs_rehash(hash_password("abc12345")) is False
