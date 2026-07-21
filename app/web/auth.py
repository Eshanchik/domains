"""Web auth routes: login, logout, home."""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.sessions import SESSION_COOKIE, SESSION_TTL_SECONDS, create_session, destroy_session
from app.db import get_session
from app.deps import current_user_optional, redis_dep, require_user
from app.models.user import User
from app.services import auth as auth_service
from app.services.auth import AuthError
from app.templating import templates

router = APIRouter(tags=["web-auth"])

_ERROR_MESSAGES = {
    AuthError.invalid: "Неверный логин или пароль.",
    AuthError.locked: "Слишком много неудачных попыток. Попробуйте позже.",
    AuthError.inactive: "Учётная запись отключена.",
    AuthError.totp_required: "Введите код из приложения-аутентификатора.",
    AuthError.totp_invalid: "Неверный код 2FA.",
}


def _is_secure() -> bool:
    """Send Secure cookies only in production (dev/test run over plain HTTP)."""
    return settings.environment == "production"


def _safe_next(value: str | None) -> str:
    """Only allow local-path redirects (no scheme, no protocol-relative) — else '/'."""
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    next: str = "",
    user: User | None = Depends(current_user_optional),
) -> HTMLResponse:
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": None,
            "need_code": False,
            "google_enabled": settings.google_oauth_enabled,
            "next_url": next,
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    code: str = Form(""),
    next: str = Form(""),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(redis_dep),
):
    result = await auth_service.authenticate(session, redis, login, password, code=code or None)
    if not result.ok:
        # 2FA required/invalid → keep the form up with the code field shown.
        need_code = result.error in (AuthError.totp_required, AuthError.totp_invalid)
        message = _ERROR_MESSAGES.get(result.error, "Ошибка входа.")
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": message,
                "need_code": need_code,
                "login_value": login,
                "google_enabled": settings.google_oauth_enabled,
                "next_url": next,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        return response

    token = await create_session(redis, result.user.id)
    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    redis: aioredis.Redis = Depends(redis_dep),
):
    token = request.cookies.get(SESSION_COOKIE, "")
    await destroy_session(redis, token)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_user),
) -> HTMLResponse:
    from app.services.dashboard import build_overview

    overview = await build_overview(session, user)
    return templates.TemplateResponse(request, "home.html", {"user": user, "ov": overview})
