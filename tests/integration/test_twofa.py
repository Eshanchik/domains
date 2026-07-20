"""TOTP 2FA: enable/disable + login second factor."""

from __future__ import annotations

import asyncio

import pyotp

from app.db import SessionLocal, get_redis
from app.models.user import Role, User
from app.services import auth as auth_service
from app.services import twofa


def _run(coro):
    return asyncio.run(coro)


def _enable_2fa(user_id: int) -> str:
    """Enable 2FA for the user; returns the TOTP secret."""

    async def _c() -> str:
        async with SessionLocal() as s:
            user = await s.get(User, user_id)
            secret = await twofa.begin_enrollment(s, user)
            ok = await twofa.enable(s, user, pyotp.TOTP(secret).now())
            assert ok
            return secret

    return _run(_c())


def test_verify_roundtrip():
    secret = twofa.generate_secret()
    assert twofa.verify(secret, pyotp.TOTP(secret).now()) is True
    assert twofa.verify(secret, "000000") is False
    assert twofa.verify(secret, "") is False


def test_enable_requires_valid_code(make_user):
    u = make_user(login="root", password="password123", role=Role.admin)

    async def run():
        async with SessionLocal() as s:
            user = await s.get(User, u["id"])
            secret = await twofa.begin_enrollment(s, user)
            bad = await twofa.enable(s, user, "000000")
            good = await twofa.enable(s, user, pyotp.TOTP(secret).now())
            return bad, good, user.totp_enabled

    bad, good, enabled = _run(run())
    assert bad is False
    assert good is True
    assert enabled is True


def test_secret_encrypted_at_rest(make_user):
    u = make_user(login="root", password="password123", role=Role.admin)
    secret = _enable_2fa(u["id"])

    async def raw():
        async with SessionLocal() as s:
            return (await s.get(User, u["id"])).totp_secret_enc

    enc = _run(raw())
    assert enc and secret not in enc  # stored encrypted


def _auth(login, password, code=None):
    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                return await auth_service.authenticate(s, redis, login, password, code=code)
        finally:
            await redis.aclose()

    return _run(run())


def test_login_second_factor_flow(make_user):
    u = make_user(login="root", password="password123", role=Role.admin)
    secret = _enable_2fa(u["id"])

    # Password ok but no code → prompt for code.
    assert _auth("root", "password123").error == auth_service.AuthError.totp_required
    # Wrong code → invalid.
    assert _auth("root", "password123", "000000").error == auth_service.AuthError.totp_invalid
    # Valid code → success.
    assert _auth("root", "password123", pyotp.TOTP(secret).now()).ok is True


def test_login_without_2fa_needs_no_code(make_user):
    make_user(login="plain", password="password123", role=Role.viewer)
    assert _auth("plain", "password123").ok is True  # no 2FA → normal login
