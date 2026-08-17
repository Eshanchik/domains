"""Registrar accounts + unassigned-domain queue (admin) (SPEC §3.4)."""

from __future__ import annotations

import ipaddress

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
    types = await svc.account_type_labels(session)
    projects = await companies_svc.list_projects(session, user)
    project_names = {p.id: p.name for p in projects}
    return templates.TemplateResponse(
        request,
        "registrars/list.html",
        {
            "user": user,
            "accounts": accounts,
            "unassigned_count": len(unassigned),
            "ip_of": svc.account_masked_ip,
            "types": types,
            "projects": projects,
            "project_names": project_names,
        },
    )


def _int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


@router.post("/registrars")
async def registrar_create(
    label: str = Form(...),
    api_user: str = Form(...),
    api_key: str = Form(...),
    username: str = Form(...),
    client_ip: str = Form(...),
    default_project_id: str = Form(""),
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
        default_project_id=_int_or_none(default_project_id),
    )
    return RedirectResponse("/registrars", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/registrars/godaddy")
async def godaddy_create(
    label: str = Form(...),
    api_key: str = Form(...),
    api_secret: str = Form(...),
    default_project_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    await svc.create_godaddy_account(
        session,
        label=label,
        api_key=api_key,
        api_secret=api_secret,
        actor_id=user.id,
        default_project_id=_int_or_none(default_project_id),
    )
    return RedirectResponse("/registrars", status_code=status.HTTP_303_SEE_OTHER)


async def _render_edit(request, session, account, connector_type, user, *, error=None):
    projects = await companies_svc.list_projects(session, user)
    return templates.TemplateResponse(
        request,
        "registrars/edit.html",
        {
            "user": user,
            "account": account,
            "connector_type": connector_type,
            "client_ip": svc.account_masked_ip(account),
            "projects": projects,
            "error": error,
        },
        status_code=status.HTTP_200_OK if error is None else status.HTTP_400_BAD_REQUEST,
    )


@router.get("/registrars/{account_id}/edit", response_class=HTMLResponse)
async def registrar_edit_form(
    request: Request,
    account_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    account = await svc.get_account(session, account_id)
    if account is None:
        return RedirectResponse("/registrars", status_code=status.HTTP_303_SEE_OTHER)
    connector_type = await svc.account_connector_type(session, account)
    return await _render_edit(request, session, account, connector_type, user)


@router.post("/registrars/{account_id}")
async def registrar_update(
    request: Request,
    account_id: int,
    label: str = Form(...),
    default_project_id: str = Form(""),
    client_ip: str = Form(""),
    api_user: str = Form(""),
    api_key: str = Form(""),
    username: str = Form(""),
    api_secret: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    account = await svc.get_account(session, account_id)
    if account is None:
        return RedirectResponse("/registrars", status_code=status.HTTP_303_SEE_OTHER)
    connector_type = await svc.account_connector_type(session, account)
    if connector_type == "godaddy":
        creds_updates = {"api_key": api_key, "api_secret": api_secret}
    else:
        # Reject a malformed Client IP at edit time instead of storing a value that only
        # fails at the next sync.
        if client_ip.strip():
            try:
                ipaddress.ip_address(client_ip.strip())
            except ValueError:
                return await _render_edit(
                    request, session, account, connector_type, user,
                    error="Неверный формат Client IP.",
                )
        creds_updates = {
            "api_user": api_user,
            "api_key": api_key,
            "username": username,
            "client_ip": client_ip,
        }
    try:
        await svc.update_account(
            session,
            account,
            label=label,
            default_project_id=_int_or_none(default_project_id),
            creds_updates=creds_updates,
            actor_id=user.id,
        )
    except svc.CredentialDecryptError:
        msg = "Не удалось расшифровать текущие креды — правка отменена (проверьте DG_MASTER_KEY)."
        return await _render_edit(request, session, account, connector_type, user, error=msg)
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
