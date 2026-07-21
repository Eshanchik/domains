"""Google OAuth sign-in routes (T37) — existing users only, 2FA still enforced.

Flow: ``/auth/google/login`` redirects to Google with a CSRF ``state`` cookie;
``/auth/google/callback`` verifies the state, exchanges the code, and looks up an
active user by the verified email. Unknown/inactive email → back to /login with an
error (no self-registration). If the user has 2FA enabled we stash a short-lived
pending token in Redis and render the code form, which posts to ``/auth/google/2fa``.
"""

from __future__ import annotations

import secrets

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.sessions import SESSION_COOKIE, SESSION_TTL_SECONDS, create_session
from app.db import get_session
from app.deps import redis_dep
from app.models.user import User
from app.services import auth as auth_service
from app.services import google_oauth, twofa
from app.templating import templates

router = APIRouter(tags=["web-oauth"])

STATE_COOKIE = "dg_oauth_state"
PENDING_COOKIE = "dg_oauth_2fa"
PENDING_TTL_SECONDS = 300


def _is_secure() -> bool:
    return settings.environment == "production"


def _pending_key(token: str) -> str:
    return f"oauth2fa:{token}"


def _redirect_uri(request: Request) -> str:
    return settings.google_redirect_uri or str(request.url_for("google_callback"))


def _login_error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": message,
            "need_code": False,
            "google_enabled": settings.google_oauth_enabled,
        },
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def _set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
    )


async def _active_user_by_email(session: AsyncSession, email: str) -> User | None:
    user = (
        await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


@router.get("/auth/google/login", name="google_login")
async def google_login(request: Request):
    if not settings.google_oauth_enabled:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    state = google_oauth.new_state()
    url = google_oauth.build_authorize_url(state=state, redirect_uri=_redirect_uri(request))
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
    )
    return response


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(redis_dep),
):
    if not settings.google_oauth_enabled:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    cookie_state = request.cookies.get(STATE_COOKIE, "")
    if (
        error
        or not code
        or not state
        or not cookie_state
        or not secrets.compare_digest(state, cookie_state)
    ):
        return _login_error(request, "Не удалось войти через Google. Повторите попытку.")

    try:
        identity = await google_oauth.exchange_code(code, _redirect_uri(request))
    except google_oauth.OAuthError:
        return _login_error(request, "Не удалось войти через Google. Повторите попытку.")

    if not identity.email_verified:
        return _login_error(request, "Google-аккаунт с неподтверждённым email не допускается.")

    user = await _active_user_by_email(session, identity.email)
    if user is None:
        return _login_error(
            request, "Нет активного пользователя с таким email. Обратитесь к администратору."
        )

    # Second factor still required if the user has it enabled.
    if user.totp_enabled:
        token = secrets.token_urlsafe(24)
        await redis.set(_pending_key(token), str(user.id), ex=PENDING_TTL_SECONDS)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "need_code": True, "oauth_2fa": True},
        )
        response.set_cookie(
            PENDING_COOKIE,
            token,
            max_age=PENDING_TTL_SECONDS,
            httponly=True,
            secure=_is_secure(),
            samesite="lax",
        )
        response.delete_cookie(STATE_COOKIE)
        return response

    token = await create_session(redis, user.id)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, token)
    response.delete_cookie(STATE_COOKIE)
    return response


@router.post("/auth/google/2fa")
async def google_2fa(
    request: Request,
    code: str = Form(""),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(redis_dep),
):
    pending = request.cookies.get(PENDING_COOKIE, "")
    user_id = await redis.get(_pending_key(pending)) if pending else None
    if not user_id:
        return _login_error(request, "Сессия входа истекла. Повторите вход через Google.")

    user = await auth_service.get_user_by_id(session, int(user_id))
    if user is None or not user.is_active or not user.totp_enabled:
        return _login_error(request, "Не удалось завершить вход.")

    if not twofa.verify(twofa.user_secret(user) or "", code):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный код 2FA.", "need_code": True, "oauth_2fa": True},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    await redis.delete(_pending_key(pending))
    token = await create_session(redis, user.id)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, token)
    response.delete_cookie(PENDING_COOKIE)
    return response
