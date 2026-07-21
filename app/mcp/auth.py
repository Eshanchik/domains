"""ASGI token-auth middleware for the MCP server.

Every HTTP request must carry ``Authorization: Bearer <DomainGuard API token>``.
The token is resolved to its (active) owning user; the user id is stashed in a
contextvar for the request so tools run with that identity. Missing/invalid tokens
get a 401 before reaching the MCP app.
"""

from __future__ import annotations

import json

from app.db import SessionLocal
from app.mcp.context import current_user_id
from app.services import api_tokens


async def _resolve_user_id(token: str) -> int | None:
    if not token:
        return None
    async with SessionLocal() as session:
        user = await api_tokens.resolve_user(session, token)
        return user.id if user is not None else None


async def _send_401(send) -> None:
    body = json.dumps({"error": "invalid or missing API token"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class TokenAuthMiddleware:
    """Authenticate every HTTP request by DomainGuard API token."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        user_id = await _resolve_user_id(token)
        if user_id is None:
            await _send_401(send)
            return
        reset_token = current_user_id.set(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_id.reset(reset_token)
