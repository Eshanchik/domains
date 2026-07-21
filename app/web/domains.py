"""Domain registry web pages (SPEC §3.2, FR-UI-2/3).

Reads are scoped; create/edit/archive/bulk require Manager (or Admin) and the target
project must be within the user's scope. CSV export mirrors the current filter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role, require_user
from app.models.user import Role, User
from app.schemas.domain import DomainCreate, DomainUpdate
from app.services import auth as auth_service
from app.services import companies as companies_svc
from app.services import domains as svc
from app.services.domains import DomainFilter
from app.templating import templates

router = APIRouter(tags=["web-domains"])
manager_required = require_role(Role.manager)


async def _project_in_scope(session: AsyncSession, user: User, project_id: int) -> bool:
    project = await companies_svc.get_project(session, project_id)
    if project is None:
        return False
    return auth_service.user_in_scope(user, company_id=project.company_id, project_id=project.id)


def _int_or_none(value: str | None) -> int | None:
    """Coerce an optional int query param, tolerating empty strings.

    The domains filter form auto-submits every field, so unset selects arrive as
    ``project_id=`` / ``expiring=`` (empty string). Treat blank (or non-numeric)
    as "no filter" instead of raising a 422.
    """
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _active_filter_qs(
    company_id: int | None,
    project_id: int | None,
    tag: str | None,
    q: str | None,
    expiring: int | None,
    archived: bool,
) -> str:
    """URL-encode only the active filter params (no sort/dir/page).

    Sort and pagination links append this so switching sort order or page keeps
    the current filters instead of resetting to "all domains".
    """
    params: list[tuple[str, str]] = []
    if company_id is not None:
        params.append(("company_id", str(company_id)))
    if project_id is not None:
        params.append(("project_id", str(project_id)))
    if tag:
        params.append(("tag", tag))
    if q:
        params.append(("q", q))
    if expiring is not None:
        params.append(("expiring", str(expiring)))
    if archived:
        params.append(("archived", "true"))
    return urlencode(params)


def _filter_from_query(
    company_id: int | None,
    project_id: int | None,
    tag: str | None,
    registrar_id: int | None,
    q: str | None,
    expiring: int | None,
    archived: bool,
    sort: str,
    direction: str,
    page: int,
    vt_detect: bool = False,
    health_down: bool = False,
) -> DomainFilter:
    return DomainFilter(
        company_id=company_id,
        project_id=project_id,
        tag=tag or None,
        registrar_id=registrar_id,
        q=q or None,
        expiring_days=expiring,
        vt_detect=vt_detect,
        health_down=health_down,
        include_archived=archived,
        sort=sort if sort in {"fqdn", "expiry_date", "tld", "created_at"} else "fqdn",
        descending=(direction == "desc"),
        page=page,
    )


@router.get("/domains", response_class=HTMLResponse)
async def domains_list(
    request: Request,
    company_id: str | None = Query(None),
    project_id: str | None = Query(None),
    tag: str | None = Query(None),
    registrar_id: str | None = Query(None),
    q: str | None = Query(None),
    expiring: str | None = Query(None),
    vt_detect: bool = Query(False),
    health_down: bool = Query(False),
    archived: bool = Query(False),
    sort: str = Query("fqdn"),
    dir: str = Query("asc"),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    company_id = _int_or_none(company_id)
    project_id = _int_or_none(project_id)
    registrar_id = _int_or_none(registrar_id)
    expiring = _int_or_none(expiring)
    flt = _filter_from_query(
        company_id,
        project_id,
        tag,
        registrar_id,
        q,
        expiring,
        archived,
        sort,
        dir,
        page,
        vt_detect=vt_detect,
        health_down=health_down,
    )
    items, total = await svc.list_domains(session, user, flt)
    companies = await companies_svc.list_companies(session, user)
    projects = await companies_svc.list_projects(session, user)
    tags = await companies_svc.list_tags(session)
    project_names = {p.id: p.name for p in projects}
    ssl = await svc.ssl_status_map(session, [d.id for d in items])
    pages = max(1, (total + flt.page_size - 1) // flt.page_size)
    filter_qs = _active_filter_qs(company_id, project_id, tag, q, expiring, archived)
    # Same filters minus project — for the project quick-switch chips.
    filter_qs_no_project = _active_filter_qs(company_id, None, tag, q, expiring, archived)
    return templates.TemplateResponse(
        request,
        "domains/list.html",
        {
            "user": user,
            "domains": items,
            "total": total,
            "page": flt.page,
            "pages": pages,
            "page_size": flt.page_size,
            "filter_qs": filter_qs,
            "filter_qs_no_project": filter_qs_no_project,
            "companies": companies,
            "projects": projects,
            "project_names": project_names,
            "ssl": ssl,
            "tags": tags,
            "f": {
                "company_id": company_id,
                "project_id": project_id,
                "tag": tag,
                "q": q,
                "expiring": expiring,
                "archived": archived,
                "sort": sort,
                "dir": dir,
            },
        },
    )


@router.get("/domains/export.csv", response_class=PlainTextResponse)
async def domains_export(
    company_id: str | None = Query(None),
    project_id: str | None = Query(None),
    tag: str | None = Query(None),
    registrar_id: str | None = Query(None),
    q: str | None = Query(None),
    expiring: str | None = Query(None),
    archived: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> PlainTextResponse:
    flt = _filter_from_query(
        _int_or_none(company_id),
        _int_or_none(project_id),
        tag,
        _int_or_none(registrar_id),
        q,
        _int_or_none(expiring),
        archived,
        "fqdn",
        "asc",
        1,
    )
    csv_text = await svc.export_csv(session, user, flt)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=domains.csv"},
    )


@router.get("/domains/new", response_class=HTMLResponse)
async def domain_new(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
) -> HTMLResponse:
    projects = await companies_svc.list_projects(session, user)
    return templates.TemplateResponse(
        request,
        "domains/form.html",
        {"user": user, "domain": None, "projects": projects, "error": None},
    )


@router.post("/domains")
async def domain_create(
    request: Request,
    fqdn: str = Form(...),
    project_id: int = Form(...),
    notes: str = Form(""),
    tags: str = Form(""),
    ssl_extra_hosts: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    if not await _project_in_scope(session, user, project_id):
        return _forbidden()
    try:
        data = DomainCreate(
            fqdn=fqdn,
            project_id=project_id,
            notes=notes or None,
            tags=[t.strip() for t in tags.split(",")],
            ssl_extra_hosts=[h.strip() for h in ssl_extra_hosts.splitlines()],
        )
        await svc.create_domain(session, data, actor_id=user.id)
    except (ValidationError, svc.DuplicateDomainError, ValueError) as exc:
        projects = await companies_svc.list_projects(session, user)
        return templates.TemplateResponse(
            request,
            "domains/form.html",
            {"user": user, "domain": None, "projects": projects, "error": _friendly(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/domains/bulk")
async def domains_bulk(
    action: str = Form(...),
    ids: list[int] = Form(default=[]),
    project_id: int | None = Form(None),
    tags: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    if action == "assign_project" and project_id is not None:
        await svc.bulk_assign_project(session, user, ids, project_id, actor_id=user.id)
    elif action == "add_tags":
        await svc.bulk_add_tags(
            session, user, ids, [t.strip() for t in tags.split(",")], actor_id=user.id
        )
    elif action == "archive":
        await svc.bulk_archive(session, user, ids, True, actor_id=user.id)
    elif action == "unarchive":
        await svc.bulk_archive(session, user, ids, False, actor_id=user.id)
    return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/domains/{domain_id}", response_class=HTMLResponse)
async def domain_card(
    request: Request,
    domain_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    domain = await svc.get_domain_with_history(session, domain_id)
    if domain is None or not await _visible(session, user, domain):
        return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)
    project = await companies_svc.get_project(session, domain.project_id)
    from sqlalchemy import select

    from app.models.alert import AlertEvent
    from app.models.check_result import CheckResult
    from app.services import healthchecks as hc_svc

    healthchecks = await hc_svc.list_for_domain(session, domain_id)
    recent_checks = list(
        (
            await session.execute(
                select(CheckResult)
                .where(CheckResult.domain_id == domain_id)
                .order_by(CheckResult.checked_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    alerts = list(
        (
            await session.execute(
                select(AlertEvent)
                .where(AlertEvent.domain_id == domain_id, AlertEvent.state == "active")
                .order_by(AlertEvent.fired_at.desc())
            )
        )
        .scalars()
        .all()
    )
    from app.services import payments as pay_svc

    payments = await pay_svc.list_for_domain(session, domain_id)
    return templates.TemplateResponse(
        request,
        "domains/card.html",
        {
            "user": user,
            "domain": domain,
            "project": project,
            "healthchecks": healthchecks,
            "recent_checks": recent_checks,
            "alerts": alerts,
            "payments": payments,
            "today": datetime.now(UTC).date(),
        },
    )


@router.get("/domains/{domain_id}/edit", response_class=HTMLResponse)
async def domain_edit(
    request: Request,
    domain_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
) -> HTMLResponse:
    domain = await svc.get_domain(session, domain_id)
    if domain is None or not await _visible(session, user, domain):
        return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)
    projects = await companies_svc.list_projects(session, user)
    return templates.TemplateResponse(
        request,
        "domains/form.html",
        {"user": user, "domain": domain, "projects": projects, "error": None},
    )


@router.post("/domains/{domain_id}")
async def domain_update(
    domain_id: int,
    notes: str = Form(""),
    expiry_date: str = Form(""),
    tags: str = Form(""),
    ssl_extra_hosts: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    domain = await svc.get_domain(session, domain_id)
    if domain is None or not await _visible(session, user, domain):
        return _forbidden()
    data = DomainUpdate(
        notes=notes or None,
        expiry_date=datetime.fromisoformat(expiry_date) if expiry_date.strip() else None,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        ssl_extra_hosts=[h.strip() for h in ssl_extra_hosts.splitlines() if h.strip()],
    )
    await svc.update_domain(session, domain, data, actor_id=user.id)
    return RedirectResponse(f"/domains/{domain_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/domains/{domain_id}/check")
async def domain_check_now(
    domain_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    """Enqueue all default checks for one domain now (row action, Manager+)."""
    domain = await svc.get_domain(session, domain_id)
    if domain is not None and await _visible(session, user, domain):
        await svc.request_immediate_checks(session, domain, actor_id=user.id)
    return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/domains/{domain_id}/archive")
async def domain_archive(
    domain_id: int,
    archived: str = Form("true"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    domain = await svc.get_domain(session, domain_id)
    if domain is not None and await _visible(session, user, domain):
        await svc.set_archived(session, domain, archived == "true", actor_id=user.id)
    return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)


async def _visible(session: AsyncSession, user: User, domain) -> bool:
    allowed = await svc.allowed_project_ids(session, user)
    return allowed is None or domain.project_id in allowed


def _forbidden() -> PlainTextResponse:
    return PlainTextResponse("Out of scope", status_code=status.HTTP_403_FORBIDDEN)


def _friendly(exc: Exception) -> str:
    if isinstance(exc, svc.DuplicateDomainError):
        return "Такой домен уже есть в реестре."
    return "Проверьте корректность домена."
