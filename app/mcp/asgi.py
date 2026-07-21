"""ASGI entrypoint for the MCP server: token-auth middleware over FastMCP's app.

Served by uvicorn (``app.mcp.asgi:app``) in the ``mcp`` container; nginx proxies the
public ``/mcp`` path to it. The streamable HTTP app carries its own lifespan (session
manager), which the middleware forwards for non-HTTP scopes.
"""

from __future__ import annotations

from app.mcp.auth import TokenAuthMiddleware
from app.mcp.server import build_mcp

mcp = build_mcp()
app = TokenAuthMiddleware(mcp.streamable_http_app())
