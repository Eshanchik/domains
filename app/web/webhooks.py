"""Outgoing webhook endpoint management (admin) — SPEC T21."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role
from app.models.user import Role, User
from app.services import webhooks as svc
from app.templating import templates

router = APIRouter(prefix="/webhooks", tags=["web-webhooks"])
admin_required = require_role(Role.admin)


@router.get("", response_class=HTMLResponse)
async def webhooks_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    endpoints = await svc.list_endpoints(session)
    return templates.TemplateResponse(
        request, "webhooks/list.html", {"user": user, "endpoints": endpoints}
    )


@router.post("")
async def webhook_create(
    url: str = Form(...),
    secret: str = Form(""),
    events: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    event_list = [e.strip() for e in events.split(",") if e.strip()]
    await svc.create_endpoint(
        session, url=url.strip(), secret=secret.strip() or None, events=event_list, actor_id=user.id
    )
    return RedirectResponse("/webhooks", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{endpoint_id}/delete")
async def webhook_delete(
    endpoint_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    endpoint = await svc.get_endpoint(session, endpoint_id)
    if endpoint is not None:
        await svc.delete_endpoint(session, endpoint, actor_id=user.id)
    return RedirectResponse("/webhooks", status_code=status.HTTP_303_SEE_OTHER)
