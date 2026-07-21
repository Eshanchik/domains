"""Google OAuth token-exchange seam: parses identity, raises on failure."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.services import google_oauth
from app.services.google_oauth import (
    TOKEN_ENDPOINT,
    USERINFO_ENDPOINT,
    OAuthError,
)


def test_build_authorize_url_has_required_params() -> None:
    url = google_oauth.build_authorize_url(state="S", redirect_uri="https://d/cb")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "state=S" in url
    assert "response_type=code" in url
    assert "scope=openid+email" in url


@respx.mock
def test_exchange_code_returns_verified_identity() -> None:
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json={"access_token": "AT"}))
    respx.get(USERINFO_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"email": "User@Corp.com", "email_verified": True})
    )
    ident = asyncio.run(google_oauth.exchange_code("code", "https://d/cb"))
    assert ident.email == "user@corp.com"  # normalized to lowercase
    assert ident.email_verified is True


@respx.mock
def test_exchange_code_token_5xx_raises() -> None:
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(503))
    with pytest.raises(OAuthError):
        asyncio.run(google_oauth.exchange_code("code", "https://d/cb"))


@respx.mock
def test_exchange_code_missing_email_raises() -> None:
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json={"access_token": "AT"}))
    respx.get(USERINFO_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"email_verified": True})
    )
    with pytest.raises(OAuthError):
        asyncio.run(google_oauth.exchange_code("code", "https://d/cb"))


@respx.mock
def test_exchange_code_no_access_token_raises() -> None:
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(OAuthError):
        asyncio.run(google_oauth.exchange_code("code", "https://d/cb"))
