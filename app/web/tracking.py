"""«Трекинг» tab — live board of tracking domains + management (T99)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.deps import NotAuthenticated, current_user_optional, require_role
from app.models.user import Role, User
from app.services import companies as companies_svc
from app.services import domains as domains_svc
from app.services import tracking as svc
from app.templating import templates

router = APIRouter(prefix="/tracking", tags=["web-tracking"])
manager_required = require_role(Role.manager)
admin_required = require_role(Role.admin)


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


async def _page(
    request: Request,
    session: AsyncSession,
    user: User,
    *,
    company_id: int | None = None,
    project_id: int | None = None,
    status_filter: str | None = None,
    q: str | None = None,
    report: svc.AddReport | None = None,
    error: str | None = None,
    form: dict | None = None,
    msg: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    allowed = await domains_svc.allowed_project_ids(session, user)
    board = await svc.build_board(
        session,
        allowed=allowed,
        company_id=company_id,
        project_id=project_id,
        status=status_filter,
        q=q,
    )
    is_admin = user.role == Role.admin
    rules = await svc.get_access_rules(session) if is_admin else []
    companies = await companies_svc.list_companies(session, user)
    return templates.TemplateResponse(
        request,
        "tracking/index.html",
        {
            "user": user,
            "board": board,
            "public": False,
            "can_manage": user.role in (Role.admin, Role.manager),
            "is_admin": is_admin,
            "companies": companies,
            "company_names": {c.id: c.name for c in companies},
            "projects": await companies_svc.list_projects(session, user),
            "f": {
                "company_id": company_id,
                "project_id": project_id,
                "status": status_filter,
                "q": q,
            },
            "statuses": svc.STATUS_LABELS,
            "template_url": (form or {}).get("url_template") or await svc.suggest_template(session),
            "form": form or {},
            "report": report,
            "error": error,
            "msg": msg,
            "rules": rules,
            "google_enabled": settings.google_oauth_enabled,
            "status_url": f"{(settings.public_base_url or '').rstrip('/')}/status/tracking",
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
async def tracking_page(
    request: Request,
    company_id: str | None = None,
    project_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    partial: str | None = None,
    msg: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(current_user_optional),
) -> HTMLResponse:
    if user is None:
        if partial:  # expired session during auto-refresh: reload the page → /login
            return HTMLResponse("", status_code=401, headers={"HX-Refresh": "true"})
        raise NotAuthenticated
    cid, pid = _int_or_none(company_id), _int_or_none(project_id)
    st = status if status in svc.STATUS_ORDER else None
    if partial:
        # HTMX auto-refresh: only the board fragment.
        allowed = await domains_svc.allowed_project_ids(session, user)
        board = await svc.build_board(
            session, allowed=allowed, company_id=cid, project_id=pid, status=st, q=q
        )
        return templates.TemplateResponse(
            request,
            "tracking/_board.html",
            {
                "board": board,
                "public": False,
                "can_manage": user.role in (Role.admin, Role.manager),
                "user": user,
            },
        )
    return await _page(
        request, session, user, company_id=cid, project_id=pid, status_filter=st, q=q, msg=msg
    )


@router.post("/add")
async def tracking_add(
    request: Request,
    fqdns: str = Form(""),
    url_template: str = Form(""),
    expected_statuses: str = Form("200-399"),
    follow_redirects: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    form = {
        "fqdns": fqdns,
        "url_template": url_template,
        "expected_statuses": expected_statuses,
        "follow_redirects": follow_redirects is not None,
    }
    names = [line for line in fqdns.replace(",", "\n").splitlines() if line.strip()]
    if not names:
        return await _page(
            request, session, user, error="Укажите хотя бы один домен.", form=form, status_code=422
        )
    try:
        report = await svc.add_to_tracking(
            session,
            fqdns=names,
            allowed=await domains_svc.allowed_project_ids(session, user),
            url_template=url_template,
            expected_statuses=expected_statuses,
            follow_redirects=follow_redirects is not None,
            actor_id=user.id,
        )
    except svc.TrackingError as exc:
        return await _page(request, session, user, error=str(exc), form=form, status_code=422)
    return await _page(request, session, user, report=report)


@router.post("/access")
async def access_add(
    request: Request,
    email_domain: str = Form(""),
    company_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    try:
        await svc.add_access_rule(
            session,
            email_domain=email_domain,
            company_id=_int_or_none(company_id),
            actor_id=user.id,
        )
    except svc.TrackingError as exc:
        return await _page(request, session, user, error=str(exc), status_code=422)
    return RedirectResponse("/tracking?msg=access#access", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/access/delete")
async def access_delete(
    email_domain: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    await svc.delete_access_rule(session, email_domain=email_domain, actor_id=user.id)
    return RedirectResponse("/tracking#access", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{domain_id}/recheck")
async def tracking_recheck(
    domain_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    n = await svc.recheck(
        session,
        domain_id,
        allowed=await domains_svc.allowed_project_ids(session, user),
        actor_id=user.id,
    )
    return RedirectResponse(f"/tracking?msg=recheck{n}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{domain_id}/remove")
async def tracking_remove(
    domain_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    removed = await svc.remove_from_tracking(
        session,
        domain_id,
        allowed=await domains_svc.allowed_project_ids(session, user),
        actor_id=user.id,
    )
    return RedirectResponse(
        f"/tracking?msg={'removed' if removed else 'noop'}", status_code=status.HTTP_303_SEE_OTHER
    )
