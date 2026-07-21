"""DomainGuard MCP server (T38).

An HTTP (streamable) MCP server that lets an assistant read and operate the domain
registry. Each request authenticates with a DomainGuard API token; every tool runs
with that token owner's role and scope (an admin token gets full super-admin power,
a viewer token is read-only). Mutations go through the same services as the web UI,
so scope checks and the audit log apply unchanged.
"""
