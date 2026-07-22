"""Admin-only user management pages (SPEC FR-UI-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role
from app.models.user import Role, User
from app.schemas.user import ScopeIn, UserCreate, UserUpdate
from app.services import auth as auth_service
from app.services import companies as companies_svc
from app.templating import templates

router = APIRouter(prefix="/users", tags=["web-users"])

# Every route here requires an admin.
admin_required = require_role(Role.admin)


def _scopes_from_form(company_ids: list[int], project_ids: list[int]) -> list[ScopeIn]:
    """Build scope grants from the checkbox lists: a company covers all its projects."""
    return [ScopeIn(company_id=c) for c in company_ids] + [
        ScopeIn(project_id=p) for p in project_ids
    ]


async def _form_context(
    session: AsyncSession, admin: User, subject: User | None, *, error: str | None = None
) -> dict[str, object]:
    """Context for the user form: full company/project lists (admin sees all) plus
    the subject's currently-granted company/project ids for the checkbox state."""
    companies = await companies_svc.list_companies(session, admin)
    projects = await companies_svc.list_projects(session, admin)
    scopes = subject.scopes if subject else []
    scoped_company_ids = {s.company_id for s in scopes if s.company_id}
    scoped_project_ids = {s.project_id for s in scopes if s.project_id}
    return {
        "user": admin,
        "subject": subject,
        "roles": list(Role),
        "error": error,
        "companies": companies,
        "projects": projects,
        "scoped_company_ids": scoped_company_ids,
        "scoped_project_ids": scoped_project_ids,
    }


@router.get("", response_class=HTMLResponse)
async def list_users(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
) -> HTMLResponse:
    users = await auth_service.list_users(session)
    return templates.TemplateResponse(request, "users/list.html", {"user": admin, "users": users})


@router.get("/new", response_class=HTMLResponse)
async def new_user_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
) -> HTMLResponse:
    ctx = await _form_context(session, admin, None)
    return templates.TemplateResponse(request, "users/form.html", ctx)


@router.post("")
async def create_user(
    request: Request,
    email: str = Form(...),
    login: str = Form(...),
    password: str = Form(...),
    role: str = Form(Role.viewer.value),
    mcp_allowed: str = Form("off"),
    company_scopes: list[int] = Form(default=[]),
    project_scopes: list[int] = Form(default=[]),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    try:
        data = UserCreate(
            email=email,
            login=login,
            password=password,
            role=Role(role),
            mcp_allowed=(mcp_allowed == "on"),
            scopes=_scopes_from_form(company_scopes, project_scopes),
        )
    except (ValidationError, ValueError) as exc:
        ctx = await _form_context(session, admin, None, error=str(exc))
        return templates.TemplateResponse(
            request, "users/form.html", ctx, status_code=status.HTTP_400_BAD_REQUEST
        )
    await auth_service.create_user(session, data, actor_id=admin.id)
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(
    request: Request,
    user_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
) -> HTMLResponse:
    subject = await auth_service.get_user_by_id(session, user_id)
    if subject is None:
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    ctx = await _form_context(session, admin, subject)
    return templates.TemplateResponse(request, "users/form.html", ctx)


@router.post("/{user_id}")
async def update_user(
    request: Request,
    user_id: int,
    email: str = Form(...),
    role: str = Form(...),
    is_active: str = Form("off"),
    mcp_allowed: str = Form("off"),
    company_scopes: list[int] = Form(default=[]),
    project_scopes: list[int] = Form(default=[]),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    user = await auth_service.get_user_by_id(session, user_id)
    if user is None:
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    data = UserUpdate(
        email=email,
        role=Role(role),
        is_active=(is_active == "on"),
        mcp_allowed=(mcp_allowed == "on"),
        scopes=_scopes_from_form(company_scopes, project_scopes),
    )
    await auth_service.update_user(session, user, data, actor_id=admin.id)
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{user_id}/password")
async def reset_password(
    user_id: int,
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    user = await auth_service.get_user_by_id(session, user_id)
    if user is not None and len(password) >= 8:
        await auth_service.set_password(session, user, password, actor_id=admin.id)
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
