"""FastMCP server exposing DomainGuard tools over streamable HTTP.

Thin wrappers: each tool loads the acting user (from the auth contextvar) and opens
a DB session, then delegates to ``app.mcp.tools``. Tool errors are raised so FastMCP
reports them to the client. ``build_mcp`` is factored out for tests.
"""

from __future__ import annotations

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.db import SessionLocal, get_redis
from app.mcp import tools
from app.mcp.oauth_provider import MCP_SCOPE, DomainGuardOAuthProvider
from app.services import auth as auth_svc

INSTRUCTIONS = (
    "DomainGuard domain-registry control. Reads (whoami, overview, list_domains, get_domain, "
    "list_alerts, list_companies, costs_summary, list_health_checks, list_payments) are scoped "
    "to your token. Manager+ writes: create_domain, update_domain, set_domain_archived, "
    "check_domain_now, resolve_alert, import_domains, add_health_check, delete_health_check, "
    "bulk_add_health_check (a {fqdn}-templated URL across many domains), add_payment. "
    "Admin-only: create_company, create_project."
)


async def _acting_user(session):
    token = get_access_token()
    if token is None or token.subject is None:
        raise tools.ToolPermissionError("no authenticated user in context")
    user = await auth_svc.get_user_by_id(session, int(token.subject))
    if user is None or not user.is_active:
        raise tools.ToolPermissionError("acting user not found or inactive")
    return user


def _auth_settings() -> AuthSettings:
    base = settings.public_base_url.rstrip("/")
    return AuthSettings(
        issuer_url=base,
        resource_server_url=f"{base}/mcp",
        required_scopes=[MCP_SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[MCP_SCOPE], default_scopes=[MCP_SCOPE]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "DomainGuard",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        auth_server_provider=DomainGuardOAuthProvider(),
        auth=_auth_settings(),
        # nginx already enforces server_name + TLS, and MCP clients connect
        # server-side (not via a victim browser), so the SDK's DNS-rebinding Host
        # check would only reject our own proxied Host. Disable it here.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

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

    @mcp.tool(description="List the HTTP health-checks configured on a domain (in scope).")
    async def list_health_checks(domain_id: int) -> dict:
        async with SessionLocal() as s:
            return await tools.list_health_checks(s, await _acting_user(s), domain_id=domain_id)

    @mcp.tool(
        description=(
            "Add an HTTP health-check to a domain (Manager+). Defaults: GET, expect 200-299, "
            "no redirects, threshold 3. Set follow_redirects and location_pattern (e.g. a 3xx "
            "range + expected Location) for redirect endpoints."
        )
    )
    async def add_health_check(
        domain_id: int,
        url: str,
        method: str = "GET",
        follow_redirects: bool = False,
        expected_statuses: str = "200-299",
        location_pattern: str | None = None,
        body_substring: str | None = None,
        timeout_s: int = 10,
        interval_min: int = 15,
        fail_threshold: int = 3,
    ) -> dict:
        async with SessionLocal() as s:
            return await tools.add_health_check(
                s,
                await _acting_user(s),
                domain_id=domain_id,
                url=url,
                method=method,
                follow_redirects=follow_redirects,
                expected_statuses=expected_statuses,
                location_pattern=location_pattern,
                body_substring=body_substring,
                timeout_s=timeout_s,
                interval_min=interval_min,
                fail_threshold=fail_threshold,
            )

    @mcp.tool(description="Delete a health-check by id (Manager+, must own its domain's scope).")
    async def delete_health_check(healthcheck_id: int) -> dict:
        async with SessionLocal() as s:
            return await tools.delete_health_check(
                s, await _acting_user(s), healthcheck_id=healthcheck_id
            )

    @mcp.tool(
        description=(
            "Add a {fqdn}-templated health-check to many domains at once (Manager+). The URL "
            "may contain {fqdn}; applied only to in-scope domains, returns applied/skipped."
        )
    )
    async def bulk_add_health_check(
        domain_ids: list[int],
        url_template: str,
        method: str = "GET",
        follow_redirects: bool = False,
        expected_statuses: str = "200-299",
        location_pattern: str | None = None,
        body_substring: str | None = None,
        timeout_s: int = 10,
        interval_min: int = 15,
        fail_threshold: int = 3,
    ) -> dict:
        async with SessionLocal() as s:
            return await tools.bulk_add_health_check(
                s,
                await _acting_user(s),
                domain_ids=domain_ids,
                url_template=url_template,
                method=method,
                follow_redirects=follow_redirects,
                expected_statuses=expected_statuses,
                location_pattern=location_pattern,
                body_substring=body_substring,
                timeout_s=timeout_s,
                interval_min=interval_min,
                fail_threshold=fail_threshold,
            )

    @mcp.tool(
        description=(
            "Update fields on a domain (Manager+): notes, auto_renew, expiry_date (ISO), "
            "renewal_price, renewal_currency, nameservers, tags, project_id. Only provided "
            "fields change; moving project_id requires the target to be in your scope."
        )
    )
    async def update_domain(
        domain_id: int,
        notes: str | None = None,
        auto_renew: bool | None = None,
        expiry_date: str | None = None,
        renewal_price: str | None = None,
        renewal_currency: str | None = None,
        nameservers: list[str] | None = None,
        tags: list[str] | None = None,
        project_id: int | None = None,
    ) -> dict:
        async with SessionLocal() as s:
            return await tools.update_domain(
                s,
                await _acting_user(s),
                domain_id=domain_id,
                notes=notes,
                auto_renew=auto_renew,
                expiry_date=expiry_date,
                renewal_price=renewal_price,
                renewal_currency=renewal_currency,
                nameservers=nameservers,
                tags=tags,
                project_id=project_id,
            )

    @mcp.tool(description="List recorded renewal payments for a domain (in scope).")
    async def list_payments(domain_id: int) -> dict:
        async with SessionLocal() as s:
            return await tools.list_payments(s, await _acting_user(s), domain_id=domain_id)

    @mcp.tool(
        description=(
            "Record a renewal payment for a domain (Manager+). Non-USD is converted to USD "
            "via the cached rate; pass rate_override if the rate API has no value."
        )
    )
    async def add_payment(
        domain_id: int,
        amount: str,
        currency: str = "USD",
        note: str | None = None,
        rate_override: str | None = None,
        paid_at: str | None = None,
    ) -> dict:
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                return await tools.add_payment(
                    s,
                    await _acting_user(s),
                    redis,
                    domain_id=domain_id,
                    amount=amount,
                    currency=currency,
                    note=note,
                    rate_override=rate_override,
                    paid_at=paid_at,
                )
        finally:
            await redis.aclose()

    @mcp.tool(description="Create a company (Admin only).")
    async def create_company(code: str, name: str) -> dict:
        async with SessionLocal() as s:
            return await tools.create_company(s, await _acting_user(s), code=code, name=name)

    @mcp.tool(description="Create a project under a company (Admin only).")
    async def create_project(company_id: int, code: str, name: str) -> dict:
        async with SessionLocal() as s:
            return await tools.create_project(
                s, await _acting_user(s), company_id=company_id, code=code, name=name
            )

    return mcp
