"""Registrar accounts + unassigned-domain queue (admin) (SPEC §3.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role
from app.models.user import Role, User
from app.services import companies as companies_svc
from app.services import registrars as svc
from app.templating import templates

router = APIRouter(tags=["web-registrars"])
admin_required = require_role(Role.admin)


@router.get("/registrars", response_class=HTMLResponse)
async def registrars_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    accounts = await svc.list_accounts(session)
    unassigned = await svc.list_unassigned(session)
    return templates.TemplateResponse(
        request,
        "registrars/list.html",
        {
            "user": user,
            "accounts": accounts,
            "unassigned_count": len(unassigned),
            "ip_of": svc.account_masked_ip,
        },
    )


@router.post("/registrars")
async def registrar_create(
    label: str = Form(...),
    api_user: str = Form(...),
    api_key: str = Form(...),
    username: str = Form(...),
    client_ip: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    await svc.create_namecheap_account(
        session,
        label=label,
        api_user=api_user,
        api_key=api_key,
        username=username,
        client_ip=client_ip,
        actor_id=user.id,
    )
    return RedirectResponse("/registrars", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/registrars/{account_id}/sync")
async def registrar_sync(
    account_id: int,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(admin_required),
):
    account = await svc.get_account(session, account_id)
    if account is not None:
        from app.workers.checks import sync_registrar_account

        sync_registrar_account.send(account_id)
    return RedirectResponse("/registrars?sync=queued", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/registrars/{account_id}/delete")
async def registrar_delete(
    account_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    account = await svc.get_account(session, account_id)
    if account is not None:
        await svc.delete_account(session, account, actor_id=user.id)
    return RedirectResponse("/registrars", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/unassigned", response_class=HTMLResponse)
async def unassigned_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    unassigned = await svc.list_unassigned(session)
    projects = await companies_svc.list_projects(session, user)
    return templates.TemplateResponse(
        request,
        "registrars/unassigned.html",
        {"user": user, "unassigned": unassigned, "projects": projects},
    )


@router.post("/unassigned/{unassigned_id}/assign")
async def unassigned_assign(
    unassigned_id: int,
    project_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    await svc.assign_to_project(session, unassigned_id, project_id, actor_id=user.id)
    return RedirectResponse("/unassigned", status_code=status.HTTP_303_SEE_OTHER)
