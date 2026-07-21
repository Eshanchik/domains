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
from app.templating import templates

router = APIRouter(prefix="/users", tags=["web-users"])

# Every route here requires an admin.
admin_required = require_role(Role.admin)


def _parse_scopes(raw: str) -> list[ScopeIn]:
    """Parse the scopes textarea: one ``company:<id>`` or ``project:<id>`` per line."""
    scopes: list[ScopeIn] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        kind, _, value = line.partition(":")
        if not value.strip().isdigit():
            continue
        num = int(value.strip())
        if kind.strip().lower() == "company":
            scopes.append(ScopeIn(company_id=num))
        elif kind.strip().lower() == "project":
            scopes.append(ScopeIn(project_id=num))
    return scopes


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
    admin: User = Depends(admin_required),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "users/form.html",
        {"user": admin, "subject": None, "roles": list(Role), "error": None},
    )


@router.post("")
async def create_user(
    request: Request,
    email: str = Form(...),
    login: str = Form(...),
    password: str = Form(...),
    role: str = Form(Role.viewer.value),
    mcp_allowed: str = Form("off"),
    scopes: str = Form(""),
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
            scopes=_parse_scopes(scopes),
        )
    except (ValidationError, ValueError) as exc:
        return templates.TemplateResponse(
            request,
            "users/form.html",
            {"user": admin, "subject": None, "roles": list(Role), "error": str(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
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
    return templates.TemplateResponse(
        request,
        "users/form.html",
        {"user": admin, "subject": subject, "roles": list(Role), "error": None},
    )


@router.post("/{user_id}")
async def update_user(
    request: Request,
    user_id: int,
    email: str = Form(...),
    role: str = Form(...),
    is_active: str = Form("off"),
    mcp_allowed: str = Form("off"),
    scopes: str = Form(""),
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
        scopes=_parse_scopes(scopes),
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
