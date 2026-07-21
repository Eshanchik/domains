"""Notification channel admin pages + test send (SPEC FR-AL-7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import redis_dep, require_role
from app.models.user import Role, User
from app.services import companies as companies_svc
from app.services import notifications as svc
from app.templating import templates

router = APIRouter(prefix="/channels", tags=["web-channels"])
admin_required = require_role(Role.admin)


@router.get("", response_class=HTMLResponse)
async def channels_list(
    request: Request,
    test: str | None = Query(None),
    sent: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "channels/list.html",
        {
            "user": user,
            "channels": await svc.list_channels(session),
            "companies": await companies_svc.list_companies(session, user),
            "projects": await companies_svc.list_projects(session, user),
            "test_result": test,
            "sent_result": sent,
            "target_of": svc.channel_target,
        },
    )


@router.post("")
async def channel_create(
    name: str = Form(...),
    type: str = Form("telegram"),
    chat_id: str = Form(""),
    webhook_url: str = Form(""),
    scope: str = Form("global"),  # "global" | "company:<id>" | "project:<id>"
    mode: str = Form("both"),
    digest_time: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    company_id = project_id = None
    is_default = scope == "global"
    if scope.startswith("company:"):
        company_id = int(scope.split(":", 1)[1])
    elif scope.startswith("project:"):
        project_id = int(scope.split(":", 1)[1])

    ctype = type if type in svc.CHANNEL_TYPES else "telegram"
    config = (
        {"chat_id": chat_id.strip()}
        if ctype == "telegram"
        else {"webhook_url": webhook_url.strip()}
    )
    await svc.create_channel_typed(
        session,
        type=ctype,
        name=name,
        config=config,
        company_id=company_id,
        project_id=project_id,
        is_default=is_default,
        mode=mode,
        digest_time=digest_time or None,
        actor_id=user.id,
    )
    return RedirectResponse("/channels", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{channel_id}/delete")
async def channel_delete(
    channel_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin_required),
):
    channel = await svc.get_channel(session, channel_id)
    if channel is not None:
        await svc.delete_channel(session, channel, actor_id=user.id)
    return RedirectResponse("/channels", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{channel_id}/test")
async def channel_test(
    channel_id: int,
    session: AsyncSession = Depends(get_session),
    redis=Depends(redis_dep),
    _admin: User = Depends(admin_required),
):
    channel = await svc.get_channel(session, channel_id)
    ok = False
    if channel is not None:
        ok = await svc.send_to_channel(
            session, redis, channel, "DomainGuard: тестовое сообщение ✅"
        )
    return RedirectResponse(
        f"/channels?test={'ok' if ok else 'fail'}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{channel_id}/send-now")
async def channel_send_now(
    channel_id: int,
    session: AsyncSession = Depends(get_session),
    redis=Depends(redis_dep),
    _admin: User = Depends(admin_required),
):
    """Compose the channel's alert digest and send it right now (manual trigger)."""
    from app.services.digest import compose_digest

    channel = await svc.get_channel(session, channel_id)
    result = "none"  # nothing to report
    if channel is not None:
        text = await compose_digest(session, channel)
        if text:
            ok = await svc.send_to_channel(session, redis, channel, text)
            result = "ok" if ok else "fail"
    return RedirectResponse(f"/channels?sent={result}", status_code=status.HTTP_303_SEE_OTHER)
