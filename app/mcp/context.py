"""Per-request authenticated identity for MCP tools.

The token-auth ASGI middleware resolves the API token to a user id and stores it in
this contextvar for the duration of the request; tools read it to load the acting
user. Using a contextvar keeps the FastMCP tool signatures clean (no request object
threaded through) while staying correct under concurrent requests.
"""

from __future__ import annotations

from contextvars import ContextVar

current_user_id: ContextVar[int | None] = ContextVar("mcp_current_user_id", default=None)
