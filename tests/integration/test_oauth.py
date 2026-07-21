"""Google OAuth sign-in: enable/disable, CSRF state, existing-user gate, 2FA."""

from __future__ import annotations

import asyncio

import pyotp
import pytest

from app.config import settings
from app.core import crypto
from app.core.sessions import SESSION_COOKIE
from app.db import SessionLocal
from app.models.user import User
from app.services import google_oauth
from app.services.google_oauth import GoogleIdentity


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def oauth_enabled(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")
    monkeypatch.setattr(settings, "google_redirect_uri", "https://dg.test/auth/google/callback")
    return settings


def _patch_identity(monkeypatch, email: str, verified: bool = True) -> None:
    async def fake_exchange(code, redirect_uri, *, client=None):
        return GoogleIdentity(email=email, email_verified=verified)

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange)


def _enable_2fa(user_id: int) -> str:
    secret = pyotp.random_base32()

    async def _a():
        async with SessionLocal() as s:
            user = await s.get(User, user_id)
            user.totp_secret_enc = crypto.encrypt(secret)
            user.totp_enabled = True
            await s.commit()

    _run(_a())
    return secret


def test_login_button_hidden_when_disabled(client) -> None:
    # No credentials configured (default) → no Google button.
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "/auth/google/login" not in resp.text


def test_login_button_shown_when_enabled(client, oauth_enabled) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "/auth/google/login" in resp.text
    assert "войти через Google" in resp.text


def test_login_redirects_to_google(client, oauth_enabled) -> None:
    resp = client.get("/auth/google/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("https://accounts.google.com/")
    assert "client_id=test-client-id" in resp.headers["location"]
    assert client.cookies.get("dg_oauth_state")  # CSRF state stored


def test_login_disabled_redirects_to_login(client) -> None:
    resp = client.get("/auth/google/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_callback_existing_user_logs_in(client, oauth_enabled, make_user, monkeypatch) -> None:
    make_user(login="alice", email="alice@corp.com")
    _patch_identity(monkeypatch, "alice@corp.com")
    client.cookies.set("dg_oauth_state", "STATE1")

    resp = client.get("/auth/google/callback?code=abc&state=STATE1", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert resp.cookies.get(SESSION_COOKIE)  # session established


def test_callback_email_case_insensitive(client, oauth_enabled, make_user, monkeypatch) -> None:
    make_user(login="bob", email="Bob@Corp.com")
    _patch_identity(monkeypatch, "bob@corp.com")
    client.cookies.set("dg_oauth_state", "S")

    resp = client.get("/auth/google/callback?code=abc&state=S", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/"


def test_callback_unknown_email_rejected(client, oauth_enabled, monkeypatch) -> None:
    _patch_identity(monkeypatch, "stranger@evil.com")
    client.cookies.set("dg_oauth_state", "S")

    resp = client.get("/auth/google/callback?code=abc&state=S", follow_redirects=False)
    assert resp.status_code == 401
    assert "администратору" in resp.text
    assert resp.cookies.get(SESSION_COOKIE) is None


def test_callback_inactive_user_rejected(client, oauth_enabled, make_user, monkeypatch) -> None:
    info = make_user(login="gone", email="gone@corp.com")

    async def _deactivate():
        async with SessionLocal() as s:
            u = await s.get(User, info["id"])
            u.is_active = False
            await s.commit()

    _run(_deactivate())
    _patch_identity(monkeypatch, "gone@corp.com")
    client.cookies.set("dg_oauth_state", "S")

    resp = client.get("/auth/google/callback?code=abc&state=S", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.cookies.get(SESSION_COOKIE) is None


def test_callback_unverified_email_rejected(client, oauth_enabled, make_user, monkeypatch) -> None:
    make_user(login="carol", email="carol@corp.com")
    _patch_identity(monkeypatch, "carol@corp.com", verified=False)
    client.cookies.set("dg_oauth_state", "S")

    resp = client.get("/auth/google/callback?code=abc&state=S", follow_redirects=False)
    assert resp.status_code == 401
    assert "неподтверждённым" in resp.text
    assert resp.cookies.get(SESSION_COOKIE) is None


def test_callback_bad_state_rejected(client, oauth_enabled, make_user, monkeypatch) -> None:
    make_user(login="dave", email="dave@corp.com")
    _patch_identity(monkeypatch, "dave@corp.com")
    client.cookies.set("dg_oauth_state", "REAL")

    # Query state does not match the cookie → CSRF rejection, no login.
    resp = client.get("/auth/google/callback?code=abc&state=FORGED", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.cookies.get(SESSION_COOKIE) is None


def test_callback_with_2fa_requires_code(client, oauth_enabled, make_user, monkeypatch) -> None:
    info = make_user(login="eve", email="eve@corp.com")
    secret = _enable_2fa(info["id"])
    _patch_identity(monkeypatch, "eve@corp.com")
    client.cookies.set("dg_oauth_state", "S")

    # Callback with 2FA enabled → no session yet; a code form + pending cookie.
    resp = client.get("/auth/google/callback?code=abc&state=S", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.cookies.get(SESSION_COOKIE) is None
    assert client.cookies.get("dg_oauth_2fa")
    assert "2fa" in resp.text.lower()

    # Wrong code → still no session.
    bad = client.post("/auth/google/2fa", data={"code": "000000"}, follow_redirects=False)
    assert bad.status_code == 401
    assert bad.cookies.get(SESSION_COOKIE) is None

    # Correct TOTP → session established.
    good = client.post(
        "/auth/google/2fa", data={"code": pyotp.TOTP(secret).now()}, follow_redirects=False
    )
    assert good.status_code == 303
    assert good.headers["location"] == "/"
    assert good.cookies.get(SESSION_COOKIE)


def test_2fa_without_pending_cookie_rejected(client, oauth_enabled) -> None:
    resp = client.post("/auth/google/2fa", data={"code": "123456"}, follow_redirects=False)
    assert resp.status_code == 401
    assert resp.cookies.get(SESSION_COOKIE) is None
