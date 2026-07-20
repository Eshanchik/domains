"""Web pages for companies, projects, and tags (SPEC FR-CO-1..3).

Reads are scoped to the current user; structural mutations are admin-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role, require_user
from app.models.user import Role, User
from app.schemas.company import (
    CompanyCreate,
    CompanyUpdate,
    ProjectCreate,
    ProjectUpdate,
)
from app.services import companies as svc
from app.templating import templates

router = APIRouter(tags=["web-companies"])
admin_required = require_role(Role.admin)


# --- Companies ---------------------------------------------------------------


@router.get("/companies", response_class=HTMLResponse)
async def companies_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    companies = await svc.list_companies(session, user)
    return templates.TemplateResponse(
        request, "companies/list.html", {"user": user, "companies": companies}
    )


@router.get("/companies/new", response_class=HTMLResponse)
async def company_new(request: Request, user: User = Depends(admin_required)) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "companies/form.html", {"user": user, "company": None, "error": None}
    )


@router.post("/companies")
async def company_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    try:
        data = CompanyCreate(name=name, code=code)
        await svc.create_company(session, data, actor_id=user.id)
    except (ValidationError, svc.DuplicateCodeError) as exc:
        return templates.TemplateResponse(
            request,
            "companies/form.html",
            {"user": user, "company": None, "error": _friendly(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse("/companies", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/companies/{company_id}/edit", response_class=HTMLResponse)
async def company_edit(
    request: Request,
    company_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    company = await svc.get_company(session, company_id)
    if company is None:
        return RedirectResponse("/companies", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "companies/form.html", {"user": user, "company": company, "error": None}
    )


@router.post("/companies/{company_id}")
async def company_update(
    company_id: int,
    name: str = Form(...),
    code: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    company = await svc.get_company(session, company_id)
    if company is not None:
        await svc.update_company(
            session, company, CompanyUpdate(name=name, code=code), actor_id=user.id
        )
    return RedirectResponse("/companies", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/companies/{company_id}/delete")
async def company_delete(
    company_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    company = await svc.get_company(session, company_id)
    if company is not None:
        await svc.delete_company(session, company, actor_id=user.id)
    return RedirectResponse("/companies", status_code=status.HTTP_303_SEE_OTHER)


# --- Projects ----------------------------------------------------------------


@router.get("/projects", response_class=HTMLResponse)
async def projects_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    projects = await svc.list_projects(session, user)
    companies = {c.id: c for c in await svc.list_companies(session, user)}
    return templates.TemplateResponse(
        request,
        "projects/list.html",
        {"user": user, "projects": projects, "companies": companies},
    )


@router.get("/projects/new", response_class=HTMLResponse)
async def project_new(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    companies = await svc.list_companies(session, user)
    return templates.TemplateResponse(
        request,
        "projects/form.html",
        {"user": user, "project": None, "companies": companies, "error": None},
    )


@router.post("/projects")
async def project_create(
    request: Request,
    company_id: int = Form(...),
    name: str = Form(...),
    code: str = Form(...),
    responsible_user_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    try:
        data = ProjectCreate(
            company_id=company_id,
            name=name,
            code=code,
            responsible_user_id=int(responsible_user_id) if responsible_user_id.strip() else None,
        )
        await svc.create_project(session, data, actor_id=user.id)
    except (ValidationError, svc.DuplicateCodeError, ValueError) as exc:
        companies = await svc.list_companies(session, user)
        return templates.TemplateResponse(
            request,
            "projects/form.html",
            {"user": user, "project": None, "companies": companies, "error": _friendly(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{project_id}/edit", response_class=HTMLResponse)
async def project_edit(
    request: Request,
    project_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    project = await svc.get_project(session, project_id)
    if project is None:
        return RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)
    companies = await svc.list_companies(session, user)
    return templates.TemplateResponse(
        request,
        "projects/form.html",
        {"user": user, "project": project, "companies": companies, "error": None},
    )


@router.post("/projects/{project_id}")
async def project_update(
    project_id: int,
    name: str = Form(...),
    code: str = Form(...),
    responsible_user_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    project = await svc.get_project(session, project_id)
    if project is not None:
        data = ProjectUpdate(
            name=name,
            code=code,
            responsible_user_id=int(responsible_user_id) if responsible_user_id.strip() else None,
        )
        await svc.update_project(session, project, data, actor_id=user.id)
    return RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/delete")
async def project_delete(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    project = await svc.get_project(session, project_id)
    if project is not None:
        await svc.delete_project(session, project, actor_id=user.id)
    return RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)


# --- Tags --------------------------------------------------------------------


@router.get("/tags", response_class=HTMLResponse)
async def tags_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    tags = await svc.list_tags(session)
    return templates.TemplateResponse(request, "tags/list.html", {"user": user, "tags": tags})


@router.post("/tags")
async def tag_create(
    name: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    if name.strip():
        try:
            await svc.create_tag(session, name.strip(), actor_id=user.id)
        except IntegrityError:
            await session.rollback()  # duplicate tag — ignore
    return RedirectResponse("/tags", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/tags/{tag_id}/delete")
async def tag_delete(
    tag_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    from app.models.company import Tag

    tag = await session.get(Tag, tag_id)
    if tag is not None:
        await svc.delete_tag(session, tag, actor_id=user.id)
    return RedirectResponse("/tags", status_code=status.HTTP_303_SEE_OTHER)


def _friendly(exc: Exception) -> str:
    if isinstance(exc, svc.DuplicateCodeError | IntegrityError):
        return "Код уже используется — выберите другой."
    return "Проверьте правильность полей."
