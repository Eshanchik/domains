"""ASGI entrypoint for the MCP server (``app.mcp.asgi:app``), served by uvicorn in
the ``mcp`` container.

The FastMCP app is OAuth-protected (T46): it mounts the OAuth metadata / authorize /
token / register / revoke routes and guards ``/mcp`` with bearer auth via the
provider's ``load_access_token`` (which also accepts DomainGuard API tokens). nginx
routes the OAuth well-known + endpoint paths and ``/mcp`` to this container.
"""

from __future__ import annotations

from app.mcp.server import build_mcp

mcp = build_mcp()
app = mcp.streamable_http_app()
