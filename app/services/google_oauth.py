"""Google OAuth2 sign-in (T37) — authorization-code flow for EXISTING users only.

No self-registration: after Google verifies the email we accept it only if a
matching, active user already exists (roles/scopes always come from our DB). The
client secret is read from the environment and never logged. The two HTTP calls to
Google (token exchange, userinfo) go through one injectable ``client`` seam so tests
mock them without real network.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"


class OAuthError(Exception):
    """Google OAuth failure (network, bad response, missing email)."""


@dataclass
class GoogleIdentity:
    email: str
    email_verified: bool


def new_state() -> str:
    """A random CSRF state token, stored in a cookie and echoed by Google."""
    return secrets.token_urlsafe(24)


def build_authorize_url(*, state: str, redirect_uri: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def _as_bool(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


async def exchange_code(
    code: str, redirect_uri: str, *, client: httpx.AsyncClient | None = None
) -> GoogleIdentity:
    """Exchange an auth code for tokens and return the verified Google identity."""
    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        token_resp = await client.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20.0,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise OAuthError("no access_token in token response")

        info_resp = await client.get(
            USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        info_resp.raise_for_status()
        info = info_resp.json()
    except httpx.HTTPError as exc:
        raise OAuthError(f"google oauth request failed: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    email = (info.get("email") or "").strip().lower()
    if not email:
        raise OAuthError("no email in userinfo")
    return GoogleIdentity(email=email, email_verified=_as_bool(info.get("email_verified")))
