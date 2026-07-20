"""Admin settings page — manage encrypted secrets (VT key, TG bot token) (FR-UI-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_role
from app.models.user import Role, User
from app.services import settings_store
from app.templating import templates

router = APIRouter(prefix="/settings", tags=["web-settings"])
admin_required = require_role(Role.admin)


@router.get("", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(admin_required),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings/page.html",
        {
            "user": _admin,
            "vt_masked": await settings_store.get_masked(session, settings_store.VT_API_KEY),
            "tg_masked": await settings_store.get_masked(
                session, settings_store.TELEGRAM_BOT_TOKEN
            ),
        },
    )


@router.post("")
async def settings_save(
    vt_api_key: str = Form(""),
    telegram_bot_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(admin_required),
):
    # Empty input leaves the existing secret unchanged (avoids wiping on re-save).
    if vt_api_key.strip():
        await settings_store.set_secret(session, settings_store.VT_API_KEY, vt_api_key.strip())
    if telegram_bot_token.strip():
        await settings_store.set_secret(
            session, settings_store.TELEGRAM_BOT_TOKEN, telegram_bot_token.strip()
        )
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)
