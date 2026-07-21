"""FastMCP server exposing DomainGuard tools over streamable HTTP.

Thin wrappers: each tool loads the acting user (from the auth contextvar) and opens
a DB session, then delegates to ``app.mcp.tools``. Tool errors are raised so FastMCP
reports them to the client. ``build_mcp`` is factored out for tests.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.db import SessionLocal
from app.mcp import tools
from app.mcp.context import current_user_id
from app.services import auth as auth_svc

INSTRUCTIONS = (
    "DomainGuard domain-registry control. Read tools (whoami, overview, list_domains, "
    "get_domain, list_alerts, list_companies, costs_summary) are scoped to your token. "
    "Write tools (create_domain, set_domain_archived, check_domain_now, resolve_alert, "
    "import_domains) require a Manager or Admin token."
)


async def _acting_user(session):
    user_id = current_user_id.get()
    if user_id is None:
        raise tools.ToolPermissionError("no authenticated user in context")
    user = await auth_svc.get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise tools.ToolPermissionError("acting user not found or inactive")
    return user


def build_mcp() -> FastMCP:
    mcp = FastMCP("DomainGuard", instructions=INSTRUCTIONS, stateless_http=True)

    @mcp.tool(description="Return the identity and role of the current API token.")
    async def whoami() -> dict:
        async with SessionLocal() as s:
            return await tools.whoami(s, await _acting_user(s))

    @mcp.tool(description="Dashboard overview: totals, expiries, SSL/VT/health problems.")
    async def overview() -> dict:
        async with SessionLocal() as s:
            return await tools.overview(s, await _acting_user(s))

    @mcp.tool(description="List domains in scope. Optional text query and expiring-days filter.")
    async def list_domains(
        q: str | None = None,
        expiring_days: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        async with SessionLocal() as s:
            return await tools.list_domains(
                s,
                await _acting_user(s),
                q=q,
                expiring_days=expiring_days,
                page=page,
                page_size=page_size,
            )

    @mcp.tool(description="Get one domain by id (must be in scope).")
    async def get_domain(domain_id: int) -> dict:
        async with SessionLocal() as s:
            return await tools.get_domain(s, await _acting_user(s), domain_id=domain_id)

    @mcp.tool(description="List active alerts in scope.")
    async def list_alerts() -> dict:
        async with SessionLocal() as s:
            return await tools.list_alerts(s, await _acting_user(s))

    @mcp.tool(description="List companies and their projects in scope.")
    async def list_companies() -> dict:
        async with SessionLocal() as s:
            return await tools.list_companies(s, await _acting_user(s))

    @mcp.tool(description="Annual renewal cost summary grouped by company/project/registrar.")
    async def costs_summary(year: int | None = None, group_by: str = "company") -> dict:
        async with SessionLocal() as s:
            return await tools.costs_summary(s, await _acting_user(s), year=year, group_by=group_by)

    @mcp.tool(description="Create a domain in a project (Manager+). Fails if it already exists.")
    async def create_domain(
        fqdn: str, project_id: int, tags: list[str] | None = None, notes: str | None = None
    ) -> dict:
        async with SessionLocal() as s:
            return await tools.create_domain(
                s, await _acting_user(s), fqdn=fqdn, project_id=project_id, tags=tags, notes=notes
            )

    @mcp.tool(description="Archive or unarchive a domain (Manager+).")
    async def set_domain_archived(domain_id: int, archived: bool) -> dict:
        async with SessionLocal() as s:
            return await tools.set_domain_archived(
                s, await _acting_user(s), domain_id=domain_id, archived=archived
            )

    @mcp.tool(description="Enqueue all checks (rdap/ssl/vt/dns) for a domain now (Manager+).")
    async def check_domain_now(domain_id: int) -> dict:
        async with SessionLocal() as s:
            return await tools.check_domain_now(s, await _acting_user(s), domain_id=domain_id)

    @mcp.tool(description="Resolve (close) an active alert by id (Manager+).")
    async def resolve_alert(alert_id: int) -> dict:
        async with SessionLocal() as s:
            return await tools.resolve_alert(s, await _acting_user(s), alert_id=alert_id)

    @mcp.tool(
        description="Import domains from CSV/plain text (Manager+). dry_run=true previews only."
    )
    async def import_domains(
        text: str, default_project_id: int | None = None, dry_run: bool = True
    ) -> dict:
        async with SessionLocal() as s:
            return await tools.import_domains(
                s,
                await _acting_user(s),
                text=text,
                default_project_id=default_project_id,
                dry_run=dry_run,
            )

    return mcp
