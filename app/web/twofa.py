"""Self-service 2FA (TOTP) enrollment pages (SPEC AUTH-1 / T22)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import require_user
from app.models.user import User
from app.services import twofa
from app.templating import templates

router = APIRouter(prefix="/2fa", tags=["web-2fa"])


@router.get("", response_class=HTMLResponse)
async def twofa_page(
    request: Request,
    user: User = Depends(require_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "twofa/page.html", {"user": user, "secret": None, "uri": None, "error": None}
    )


@router.post("/begin", response_class=HTMLResponse)
async def twofa_begin(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    secret = await twofa.begin_enrollment(session, user)
    uri = twofa.provisioning_uri(secret, user.login)
    return templates.TemplateResponse(
        request, "twofa/page.html", {"user": user, "secret": secret, "uri": uri, "error": None}
    )


@router.post("/enable")
async def twofa_enable(
    request: Request,
    code: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
):
    if await twofa.enable(session, user, code):
        return RedirectResponse("/2fa", status_code=status.HTTP_303_SEE_OTHER)
    # Re-show with the current pending secret so the user can retry.
    secret = twofa.user_secret(user)
    uri = twofa.provisioning_uri(secret, user.login) if secret else None
    return templates.TemplateResponse(
        request,
        "twofa/page.html",
        {"user": user, "secret": secret, "uri": uri, "error": "Неверный код, попробуйте ещё раз."},
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@router.post("/disable")
async def twofa_disable(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
):
    await twofa.disable(session, user)
    return RedirectResponse("/2fa", status_code=status.HTTP_303_SEE_OTHER)
