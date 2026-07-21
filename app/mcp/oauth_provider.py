"""OAuth 2.1 authorization-server provider for the MCP server (T46).

Implements the MCP SDK's provider interface backed by Redis (``oauth_store``). The
``authorize`` step hands off to our own consent page (``/oauth/consent`` in the api
app) so the user logs in and approves under an account that is allowed MCP; the
consent page mints the authorization code. ``load_access_token`` also accepts a plain
DomainGuard API token (``dg_…``) so the header-based flow keeps working alongside
OAuth. Every issued token carries the user id in ``subject``.
"""

from __future__ import annotations

import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config import settings
from app.db import SessionLocal
from app.mcp import oauth_store as store

MCP_SCOPE = "mcp"


def _now() -> int:
    return int(time.time())


class DomainGuardOAuthProvider(OAuthAuthorizationServerProvider):
    """Redis-backed provider; consent handled by our own login-gated page."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await store.put_client(client_info)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Stash the request and redirect the browser to our consent page."""
        rid = secrets.token_urlsafe(24)
        await store.put_authreq(
            rid,
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "scopes": list(params.scopes or [MCP_SCOPE]),
                "state": params.state,
                "code_challenge": params.code_challenge,
                "resource": params.resource,
            },
        )
        return f"{settings.public_base_url.rstrip('/')}/oauth/consent?rid={rid}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = await store.get_code(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # The SDK has already verified PKCE + redirect_uri. Codes are single-use.
        await store.drop_code(authorization_code.code)
        return await self._issue(
            client_id=client.client_id,
            subject=authorization_code.subject,
            scopes=authorization_code.scopes,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        stored = await store.get_access(token)
        if stored is not None:
            if stored.expires_at and stored.expires_at < _now():
                return None
            return stored
        # Fallback: a DomainGuard API token (dg_…) → synthesize an access token.
        from app.services import api_tokens

        async with SessionLocal() as session:
            user = await api_tokens.resolve_user(session, token)
            if user is None:
                return None
            return AccessToken(
                token=token, client_id="api-token", scopes=[MCP_SCOPE], subject=str(user.id)
            )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        stored = await store.get_refresh(refresh_token)
        if stored is None or stored.client_id != client.client_id:
            return None
        return stored

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        await store.drop_token(refresh_token.token)
        return await self._issue(
            client_id=client.client_id,
            subject=refresh_token.subject,
            scopes=scopes or refresh_token.scopes,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await store.drop_token(token.token)

    async def _issue(self, *, client_id: str, subject: str | None, scopes: list[str]) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        await store.put_access(
            AccessToken(
                token=access,
                client_id=client_id,
                scopes=scopes,
                expires_at=_now() + store.ACCESS_TTL,
                subject=subject,
            )
        )
        await store.put_refresh(
            RefreshToken(
                token=refresh,
                client_id=client_id,
                scopes=scopes,
                expires_at=_now() + store.REFRESH_TTL,
                subject=subject,
            )
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=store.ACCESS_TTL,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )
