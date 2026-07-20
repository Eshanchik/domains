"""Shared FastAPI dependencies for auth and access control.

Used by both the HTML (web) and REST (api) layers so authorization logic lives in
one place. Web routers turn a ``NotAuthenticated`` error into a redirect to /login
via an exception handler (see ``app.main``); the API layer can map it to 401.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import SESSION_COOKIE, get_session_user
from app.db import get_redis, get_session
from app.models.user import Role, User
from app.services import auth as auth_service


class NotAuthenticated(Exception):
    """Raised when a protected endpoint is reached without a valid session."""


async def redis_dep() -> AsyncIterator[aioredis.Redis]:
    """Yield a Redis client and close it after the request."""
    client = get_redis()
    try:
        yield client
    finally:
        await client.aclose()


async def current_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(redis_dep),
) -> User | None:
    """Resolve the logged-in user from the session cookie, or None."""
    token = request.cookies.get(SESSION_COOKIE, "")
    user_id = await get_session_user(redis, token)
    if user_id is None:
        return None
    user = await auth_service.get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def require_user(
    user: User | None = Depends(current_user_optional),
) -> User:
    """Require an authenticated, active user."""
    if user is None:
        raise NotAuthenticated
    return user


def require_role(*roles: Role):
    """Dependency factory enforcing that the user has one of ``roles``.

    ``admin`` always passes. Insufficient privilege raises HTTP 403.
    """

    async def _dep(user: User = Depends(require_user)) -> User:
        if user.role == Role.admin or user.role in roles:
            return user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return _dep


def require_scope(*, company_id: int | None = None, project_id: int | None = None):
    """Dependency factory enforcing company/project scope on the current user."""

    async def _dep(user: User = Depends(require_user)) -> User:
        if auth_service.user_in_scope(user, company_id=company_id, project_id=project_id):
            return user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Out of scope")

    return _dep
