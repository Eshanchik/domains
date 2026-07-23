"""MCP tool implementations as plain, testable async functions.

Each takes ``(session, user, ...)`` and returns JSON-able data. Reads are scoped to
the user; mutations require Manager+ (``_require_manager``) and go through the same
services as the web UI, so audit logging and scope checks are automatic. The FastMCP
wrappers in ``server.py`` just supply the session and the acting user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.models.healthcheck import HealthCheck
from app.models.payment import Payment
from app.models.user import Role, User
from app.schemas.company import CompanyCreate, ProjectCreate
from app.schemas.domain import DomainCreate, DomainUpdate
from app.schemas.healthcheck import HealthCheckCreate
from app.services import alerts as alerts_svc
from app.services import auth as auth_svc
from app.services import companies as companies_svc
from app.services import domains as domains_svc
from app.services import healthchecks as healthchecks_svc
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


def _require_admin(user: User) -> None:
    if user.role != Role.admin:
        raise ToolPermissionError("this action requires Admin role")


async def _visible(session: AsyncSession, user: User, domain: Domain) -> bool:
    allowed = await domains_svc.allowed_project_ids(session, user)
    return allowed is None or domain.project_id in allowed


async def _domain_in_scope(session: AsyncSession, user: User, domain_id: int) -> Domain:
    """Load a domain by id, enforcing scope; raise ToolInputError if missing/out of scope."""
    domain = await domains_svc.get_domain(session, domain_id)
    if domain is None or not await _visible(session, user, domain):
        raise ToolInputError(f"domain {domain_id} not found or out of scope")
    return domain


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


def _hc_dict(hc: HealthCheck) -> dict:
    return {
        "id": hc.id,
        "domain_id": hc.domain_id,
        "url": hc.url,
        "method": hc.method,
        "follow_redirects": hc.follow_redirects,
        "expected_statuses": hc.expected_statuses,
        "location_pattern": hc.location_pattern,
        "body_substring": hc.body_substring,
        "state": hc.state,
        "consecutive_failures": hc.consecutive_failures,
        "is_enabled": hc.is_enabled,
        "last_checked_at": hc.last_checked_at.isoformat() if hc.last_checked_at else None,
    }


def _payment_dict(p: Payment) -> dict:
    return {
        "id": p.id,
        "domain_id": p.domain_id,
        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        "amount": str(p.amount),
        "currency": p.currency,
        "amount_usd": str(p.amount_usd),
        "note": p.note,
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
        .where(AlertEvent.state == "active", Domain.is_active.is_(True))
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


# --- health-checks -----------------------------------------------------------


async def list_health_checks(session: AsyncSession, user: User, *, domain_id: int) -> dict:
    await _domain_in_scope(session, user, domain_id)
    items = await healthchecks_svc.list_for_domain(session, domain_id)
    return {"items": [_hc_dict(hc) for hc in items]}


def _hc_template(
    url: str,
    method: str,
    follow_redirects: bool,
    expected_statuses: str,
    location_pattern: str | None,
    body_substring: str | None,
    timeout_s: int,
    interval_min: int,
    fail_threshold: int,
) -> HealthCheckCreate:
    try:
        return HealthCheckCreate(
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
    except ValueError as exc:
        raise ToolInputError(f"invalid health-check parameters: {exc}") from exc


async def add_health_check(
    session: AsyncSession,
    user: User,
    *,
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
    _require_manager(user)
    await _domain_in_scope(session, user, domain_id)
    data = _hc_template(
        url,
        method,
        follow_redirects,
        expected_statuses,
        location_pattern,
        body_substring,
        timeout_s,
        interval_min,
        fail_threshold,
    )
    try:
        hc = await healthchecks_svc.create(session, domain_id, data, actor_id=user.id)
    except healthchecks_svc.InvalidHealthCheckUrl as exc:
        raise ToolInputError(f"unsafe or invalid URL: {exc}") from exc
    return _hc_dict(hc)


async def delete_health_check(session: AsyncSession, user: User, *, healthcheck_id: int) -> dict:
    _require_manager(user)
    hc = await healthchecks_svc.get(session, healthcheck_id)
    if hc is None:
        raise ToolInputError(f"health-check {healthcheck_id} not found")
    # Scope is enforced through the check's owning domain.
    await _domain_in_scope(session, user, hc.domain_id)
    await healthchecks_svc.delete(session, hc, actor_id=user.id)
    return {"id": healthcheck_id, "deleted": True}


async def bulk_add_health_check(
    session: AsyncSession,
    user: User,
    *,
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
    """Apply a ``{fqdn}``-templated health-check to many domains, restricted to scope."""
    _require_manager(user)
    template = _hc_template(
        url_template,
        method,
        follow_redirects,
        expected_statuses,
        location_pattern,
        body_substring,
        timeout_s,
        interval_min,
        fail_threshold,
    )
    allowed = await domains_svc.allowed_project_ids(session, user)
    stmt = select(Domain).where(Domain.id.in_(domain_ids or []))
    if allowed is not None:
        if not allowed:
            return {"applied": 0, "skipped": list(domain_ids or [])}
        stmt = stmt.where(Domain.project_id.in_(allowed))
    visible = list((await session.execute(stmt)).scalars().all())
    visible_ids = [d.id for d in visible]
    skipped = [did for did in (domain_ids or []) if did not in visible_ids]
    applied = await healthchecks_svc.bulk_add_template(
        session, visible_ids, template, actor_id=user.id
    )
    return {"applied": applied, "skipped": skipped}


# --- domain editing ----------------------------------------------------------


async def update_domain(
    session: AsyncSession,
    user: User,
    *,
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
    """Update only the fields provided (others are left untouched). Reassigning the
    project is allowed only if the target project is within the caller's scope."""
    _require_manager(user)
    domain = await _domain_in_scope(session, user, domain_id)

    # Collect only the fields the caller actually supplied (None == "leave as is").
    provided: dict[str, object] = {}
    for name, value in (
        ("notes", notes),
        ("auto_renew", auto_renew),
        ("expiry_date", expiry_date),
        ("renewal_price", renewal_price),
        ("renewal_currency", renewal_currency),
        ("nameservers", nameservers),
        ("tags", tags),
        ("project_id", project_id),
    ):
        if value is not None:
            provided[name] = value
    if not provided:
        raise ToolInputError("no fields to update")

    # A project move must land inside the caller's scope (mirror create_domain / T52).
    if "project_id" in provided and provided["project_id"] != domain.project_id:
        target = await companies_svc.get_project(session, int(provided["project_id"]))
        if target is None:
            raise ToolInputError(f"project {provided['project_id']} not found")
        if not auth_svc.user_in_scope(user, company_id=target.company_id, project_id=target.id):
            raise ToolPermissionError("target project is out of your scope")

    try:
        data = DomainUpdate(**provided)
    except (ValueError, InvalidOperation) as exc:
        raise ToolInputError(f"invalid field value: {exc}") from exc
    updated = await domains_svc.update_domain(session, domain, data, actor_id=user.id)
    return _domain_dict(updated)


# --- payments ----------------------------------------------------------------


async def list_payments(session: AsyncSession, user: User, *, domain_id: int) -> dict:
    await _domain_in_scope(session, user, domain_id)
    items = await payments_svc.list_for_domain(session, domain_id)
    return {"items": [_payment_dict(p) for p in items]}


async def add_payment(
    session: AsyncSession,
    user: User,
    redis: aioredis.Redis,
    *,
    domain_id: int,
    amount: str,
    currency: str = "USD",
    note: str | None = None,
    rate_override: str | None = None,
    paid_at: str | None = None,
) -> dict:
    _require_manager(user)
    await _domain_in_scope(session, user, domain_id)
    try:
        amount_dec = Decimal(str(amount))
        rate_dec = Decimal(str(rate_override)) if rate_override is not None else None
        when = datetime.fromisoformat(paid_at) if paid_at else None
    except (InvalidOperation, ValueError) as exc:
        raise ToolInputError(f"invalid amount/rate/date: {exc}") from exc
    try:
        payment = await payments_svc.add_payment(
            session,
            redis,
            domain_id=domain_id,
            amount=amount_dec,
            currency=currency,
            paid_at=when,
            note=note,
            rate_override=rate_dec,
            actor_id=user.id,
        )
    except payments_svc.RateUnavailableError as exc:
        raise ToolInputError(
            f"no exchange rate for {exc}; pass rate_override to record it"
        ) from exc
    return _payment_dict(payment)


# --- structure (Admin) -------------------------------------------------------


async def create_company(session: AsyncSession, user: User, *, code: str, name: str) -> dict:
    _require_admin(user)
    try:
        company = await companies_svc.create_company(
            session, CompanyCreate(code=code, name=name), actor_id=user.id
        )
    except companies_svc.DuplicateCodeError as exc:
        raise ToolInputError(f"company code already exists: {exc}") from exc
    except ValueError as exc:
        raise ToolInputError(f"invalid company: {exc}") from exc
    return {"id": company.id, "code": company.code, "name": company.name}


async def create_project(
    session: AsyncSession, user: User, *, company_id: int, code: str, name: str
) -> dict:
    _require_admin(user)
    if await companies_svc.get_company(session, company_id) is None:
        raise ToolInputError(f"company {company_id} not found")
    try:
        project = await companies_svc.create_project(
            session, ProjectCreate(company_id=company_id, code=code, name=name), actor_id=user.id
        )
    except companies_svc.DuplicateCodeError as exc:
        raise ToolInputError(f"project code already exists: {exc}") from exc
    except ValueError as exc:
        raise ToolInputError(f"invalid project: {exc}") from exc
    return {
        "id": project.id,
        "company_id": project.company_id,
        "code": project.code,
        "name": project.name,
    }
