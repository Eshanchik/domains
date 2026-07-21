"""Health-check web routes: per-domain CRUD + template bulk-add (FR-CK-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role
from app.models.user import Role, User
from app.schemas.healthcheck import HealthCheckCreate
from app.services import companies as companies_svc
from app.services import domains as domains_svc
from app.services import healthchecks as svc
from app.templating import templates

router = APIRouter(tags=["web-healthchecks"])
manager_required = require_role(Role.manager)


async def _domain_visible(session: AsyncSession, user: User, domain_id: int) -> bool:
    allowed = await domains_svc.allowed_project_ids(session, user)
    domain = await domains_svc.get_domain(session, domain_id)
    if domain is None:
        return False
    return allowed is None or domain.project_id in allowed


def _form_to_schema(
    url: str,
    method: str,
    follow_redirects: str,
    expected_statuses: str,
    location_pattern: str,
    body_substring: str,
    timeout_s: int,
    interval_min: int,
    fail_threshold: int,
) -> HealthCheckCreate:
    return HealthCheckCreate(
        url=url,
        method=method,
        follow_redirects=(follow_redirects == "on"),
        expected_statuses=expected_statuses or "200-299",
        location_pattern=location_pattern or None,
        body_substring=body_substring or None,
        timeout_s=timeout_s,
        interval_min=interval_min,
        fail_threshold=fail_threshold,
    )


@router.post("/domains/{domain_id}/healthchecks")
async def create_healthcheck(
    domain_id: int,
    url: str = Form(...),
    method: str = Form("GET"),
    follow_redirects: str = Form("off"),
    expected_statuses: str = Form("200-299"),
    location_pattern: str = Form(""),
    body_substring: str = Form(""),
    timeout_s: int = Form(10),
    interval_min: int = Form(15),
    fail_threshold: int = Form(3),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    if not await _domain_visible(session, user, domain_id):
        return PlainTextResponse("Out of scope", status_code=status.HTTP_403_FORBIDDEN)
    data = _form_to_schema(
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
        await svc.create(session, domain_id, data, actor_id=user.id)
    except svc.InvalidHealthCheckUrl:
        return PlainTextResponse(
            "Недопустимый URL: разрешены только http(s) и публичные адреса.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse(f"/domains/{domain_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/healthchecks/{hc_id}/delete")
async def delete_healthcheck(
    hc_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    hc = await svc.get(session, hc_id)
    if hc is not None and await _domain_visible(session, user, hc.domain_id):
        domain_id = hc.domain_id
        await svc.delete(session, hc, actor_id=user.id)
        return RedirectResponse(f"/domains/{domain_id}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/healthchecks/bulk", response_class=HTMLResponse)
async def bulk_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
) -> HTMLResponse:
    flt = domains_svc.DomainFilter(page_size=500)
    domains, _ = await domains_svc.list_domains(session, user, flt)
    return templates.TemplateResponse(
        request, "healthchecks/bulk.html", {"user": user, "domains": domains}
    )


@router.post("/healthchecks/bulk")
async def bulk_apply(
    domain_ids: list[int] = Form(default=[]),
    url: str = Form(...),
    method: str = Form("GET"),
    follow_redirects: str = Form("off"),
    expected_statuses: str = Form("200-299"),
    location_pattern: str = Form(""),
    body_substring: str = Form(""),
    timeout_s: int = Form(10),
    interval_min: int = Form(15),
    fail_threshold: int = Form(3),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(manager_required),
):
    # Restrict to visible domains only.
    allowed = await domains_svc.allowed_project_ids(session, user)
    visible_ids = []
    for did in domain_ids:
        d = await domains_svc.get_domain(session, did)
        if d is not None and (allowed is None or d.project_id in allowed):
            visible_ids.append(did)
    data = _form_to_schema(
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
    await svc.bulk_add_template(session, visible_ids, data, actor_id=user.id)
    return RedirectResponse("/domains", status_code=status.HTTP_303_SEE_OTHER)


# Referenced by the domain card to render health-checks.
async def healthchecks_for_card(session: AsyncSession, domain_id: int):
    return await svc.list_for_domain(session, domain_id)


__all__ = ["router", "healthchecks_for_card", "companies_svc"]
