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
}


def _is_secure() -> bool:
    """Send Secure cookies only in production (dev/test run over plain HTTP)."""
    return settings.environment == "production"


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    user: User | None = Depends(current_user_optional),
) -> HTMLResponse:
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(redis_dep),
):
    result = await auth_service.authenticate(session, redis, login, password)
    if not result.ok:
        message = _ERROR_MESSAGES.get(result.error, "Ошибка входа.")
        response = templates.TemplateResponse(
            request, "login.html", {"error": message}, status_code=status.HTTP_401_UNAUTHORIZED
        )
        return response

    token = await create_session(redis, result.user.id)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
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
    user: User = Depends(require_user),
) -> HTMLResponse:
    return templates.TemplateResponse(request, "home.html", {"user": user})
