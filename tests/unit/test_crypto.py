"""Fernet secret encryption."""

from __future__ import annotations

import pytest

from app.core import crypto


def test_encrypt_decrypt_roundtrip() -> None:
    token = crypto.encrypt("super-secret-key")
    assert token != "super-secret-key"
    assert crypto.decrypt(token) == "super-secret-key"


def test_decrypt_invalid_raises() -> None:
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt("not-a-valid-token")


def test_mask() -> None:
    assert crypto.mask("abcdefgh") == "abcd***"
    assert crypto.mask("ab") == "**"
    assert crypto.mask(None) == ""
    assert crypto.mask("") == ""
