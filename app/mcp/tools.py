"""MCP tool implementations as plain, testable async functions.

Each takes ``(session, user, ...)`` and returns JSON-able data. Reads are scoped to
the user; mutations require Manager+ (``_require_manager``) and go through the same
services as the web UI, so audit logging and scope checks are automatic. The FastMCP
wrappers in ``server.py`` just supply the session and the acting user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.models.user import Role, User
from app.schemas.domain import DomainCreate
from app.services import alerts as alerts_svc
from app.services import companies as companies_svc
from app.services import domains as domains_svc
from app.services import import_domains as import_svc
from app.services import payments as payments_svc
from app.services.dashboard import build_overview
from app.services.domains import DomainFilter


class ToolPermissionError(Exception):
    """The acting token lacks the role or scope for this tool."""


class ToolInputError(Exception):
    """Invalid arguments (missing entity, bad value)."""


def _require_manager(user: User) -> None:
    if user.role not in (Role.admin, Role.manager):
        raise ToolPermissionError("this action requires Manager or Admin role")


async def _visible(session: AsyncSession, user: User, domain: Domain) -> bool:
    allowed = await domains_svc.allowed_project_ids(session, user)
    return allowed is None or domain.project_id in allowed


def _domain_dict(d: Domain) -> dict:
    return {
        "id": d.id,
        "fqdn": d.fqdn,
        "project_id": d.project_id,
        "tld": d.tld,
        "expiry_date": d.expiry_date.isoformat() if d.expiry_date else None,
        "auto_renew": d.auto_renew,
        "is_active": d.is_active,
        "renewal_price": str(d.renewal_price) if d.renewal_price is not None else None,
        "renewal_currency": d.renewal_currency,
        "nameservers": list(d.nameservers or []),
        "notes": d.notes,
    }


# --- read tools --------------------------------------------------------------


async def whoami(session: AsyncSession, user: User) -> dict:
    return {"id": user.id, "login": user.login, "email": user.email, "role": user.role.value}


async def overview(session: AsyncSession, user: User) -> dict:
    ov = await build_overview(session, user)
    return {
        "total": ov.total,
        "expiring_7": ov.expiring_7,
        "expiring_30": ov.expiring_30,
        "expiring_90": ov.expiring_90,
        "ssl_problems": ov.ssl_problems,
        "vt_detects": ov.vt_detects,
        "health_down": ov.health_down,
        "by_company": [
            {"name": r.name, "domains": r.domains, "expiring_30": r.expiring_30}
            for r in ov.by_company
        ],
    }


async def list_domains(
    session: AsyncSession,
    user: User,
    *,
    q: str | None = None,
    expiring_days: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    flt = DomainFilter(
        q=q or None,
        expiring_days=expiring_days,
        page=max(1, page),
        page_size=min(max(1, page_size), 500),
    )
    items, total = await domains_svc.list_domains(session, user, flt)
    return {
        "total": total,
        "page": flt.page,
        "page_size": flt.page_size,
        "items": [_domain_dict(d) for d in items],
    }


async def get_domain(session: AsyncSession, user: User, *, domain_id: int) -> dict:
    domain = await domains_svc.get_domain(session, domain_id)
    if domain is None or not await _visible(session, user, domain):
        raise ToolInputError(f"domain {domain_id} not found or out of scope")
    return _domain_dict(domain)


async def list_alerts(session: AsyncSession, user: User) -> dict:
    allowed = await domains_svc.allowed_project_ids(session, user)
    stmt = (
        select(AlertEvent, Domain.fqdn)
        .join(Domain, Domain.id == AlertEvent.domain_id)
        .where(AlertEvent.state == "active")
        .order_by(AlertEvent.fired_at.desc())
    )
    if allowed is not None:
        stmt = (
            stmt.where(Domain.project_id.in_(allowed))
            if allowed
            else stmt.where(Domain.id.is_(None))
        )
    rows = (await session.execute(stmt)).all()
    return {
        "items": [
            {
                "id": e.id,
                "kind": e.kind,
                "severity": e.severity,
                "domain": fqdn,
                "fired_at": e.fired_at.isoformat() if e.fired_at else None,
                "payload": e.payload_json or {},
            }
            for e, fqdn in rows
        ]
    }


async def list_companies(session: AsyncSession, user: User) -> dict:
    companies = await companies_svc.list_companies(session, user)
    projects = await companies_svc.list_projects(session, user)
    by_company: dict[int, list[dict]] = {}
    for p in projects:
        by_company.setdefault(p.company_id, []).append({"id": p.id, "name": p.name})
    return {
        "items": [
            {"id": c.id, "name": c.name, "projects": by_company.get(c.id, [])} for c in companies
        ]
    }


async def costs_summary(
    session: AsyncSession, user: User, *, year: int | None = None, group_by: str = "company"
) -> dict:
    yr = year or datetime.now(UTC).year
    start = datetime(yr, 1, 1, tzinfo=UTC)
    end = datetime(yr + 1, 1, 1, tzinfo=UTC)
    rows = await payments_svc.cost_summary(session, user, start=start, end=end, group_by=group_by)
    total = sum((r.total_usd for r in rows), Decimal(0))
    return {
        "year": yr,
        "group_by": group_by,
        "total_usd": str(total),
        "rows": [{"label": r.label, "total_usd": str(r.total_usd)} for r in rows],
    }


# --- write tools (Manager+) --------------------------------------------------


async def create_domain(
    session: AsyncSession,
    user: User,
    *,
    fqdn: str,
    project_id: int,
    tags: list[str] | None = None,
    notes: str | None = None,
) -> dict:
    _require_manager(user)
    project = await companies_svc.get_project(session, project_id)
    if project is None:
        raise ToolInputError(f"project {project_id} not found")
    from app.services import auth as auth_svc

    if not auth_svc.user_in_scope(user, company_id=project.company_id, project_id=project.id):
        raise ToolPermissionError("project is out of your scope")
    try:
        data = DomainCreate(fqdn=fqdn, project_id=project_id, notes=notes or None, tags=tags or [])
        domain = await domains_svc.create_domain(session, data, actor_id=user.id)
    except domains_svc.DuplicateDomainError as exc:
        raise ToolInputError(f"domain already exists: {exc}") from exc
    return _domain_dict(domain)


async def set_domain_archived(
    session: AsyncSession, user: User, *, domain_id: int, archived: bool
) -> dict:
    _require_manager(user)
    domain = await domains_svc.get_domain(session, domain_id)
    if domain is None or not await _visible(session, user, domain):
        raise ToolInputError(f"domain {domain_id} not found or out of scope")
    await domains_svc.set_archived(session, domain, archived, actor_id=user.id)
    return {"id": domain.id, "fqdn": domain.fqdn, "is_active": domain.is_active}


async def check_domain_now(session: AsyncSession, user: User, *, domain_id: int) -> dict:
    _require_manager(user)
    domain = await domains_svc.get_domain(session, domain_id)
    if domain is None or not await _visible(session, user, domain):
        raise ToolInputError(f"domain {domain_id} not found or out of scope")
    dispatched = await domains_svc.request_immediate_checks(session, domain, actor_id=user.id)
    return {"id": domain.id, "fqdn": domain.fqdn, "enqueued": dispatched}


async def resolve_alert(session: AsyncSession, user: User, *, alert_id: int) -> dict:
    _require_manager(user)
    event = await session.get(AlertEvent, alert_id)
    if event is None:
        raise ToolInputError(f"alert {alert_id} not found")
    domain = await domains_svc.get_domain(session, event.domain_id)
    if domain is None or not await _visible(session, user, domain):
        raise ToolPermissionError("alert is out of your scope")
    resolved = await alerts_svc.resolve_event(session, alert_id)
    return {"id": alert_id, "resolved": resolved}


async def import_domains(
    session: AsyncSession,
    user: User,
    *,
    text: str,
    default_project_id: int | None = None,
    dry_run: bool = True,
) -> dict:
    _require_manager(user)
    # Auto-detect: a CSV with an ``fqdn`` header carries per-row project codes;
    # otherwise treat each line as a bare domain assigned to ``default_project_id``.
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    is_csv = "fqdn" in first_line.lower()
    rows = import_svc.parse_csv(text) if is_csv else import_svc.parse_bulk(text)
    report = await import_svc.run_import(
        session,
        user,
        rows,
        default_project_id=default_project_id,
        source="api-mcp",
        actor_id=user.id,
        dry_run=dry_run,
    )
    return {
        "dry_run": dry_run,
        "created": report.created,
        "updated": report.updated,
        "errors": report.errors,
        "rows": [{"fqdn": r.fqdn, "action": r.action, "message": r.message} for r in report.rows],
    }
