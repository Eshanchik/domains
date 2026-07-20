"""Authentication and user-management services.

All password checks go through argon2; login attempts are guarded against
brute force; user mutations are written to the audit log.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import login_guard
from app.core.audit import record_audit
from app.core.security import hash_password, needs_rehash, verify_password
from app.models.user import Role, User, UserScope
from app.schemas.user import ScopeIn, UserCreate, UserUpdate


class AuthError(enum.StrEnum):
    invalid = "invalid"
    locked = "locked"
    inactive = "inactive"


@dataclass
class AuthResult:
    user: User | None = None
    error: AuthError | None = None

    @property
    def ok(self) -> bool:
        return self.user is not None


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_login(session: AsyncSession, login: str) -> User | None:
    result = await session.execute(select(User).where(User.login == login))
    return result.scalar_one_or_none()


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.id))
    return list(result.scalars().all())


async def authenticate(
    session: AsyncSession,
    redis: aioredis.Redis,
    login: str,
    password: str,
) -> AuthResult:
    """Verify credentials with brute-force protection.

    Returns a locked/invalid/inactive error rather than raising so the caller can
    render a generic message (never reveal which factor failed).
    """
    if await login_guard.is_locked(redis, login):
        return AuthResult(error=AuthError.locked)

    user = await get_user_by_login(session, login)
    # Always run a verify to keep timing roughly constant even for unknown logins.
    stored_hash = (
        user.password_hash
        if user
        else (
            "$argon2id$v=19$m=65536,t=3,p=4$"  # dummy prefix, verify will simply fail
            "AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        )
    )
    password_ok = verify_password(password, stored_hash)

    if user is None or not password_ok:
        count = await login_guard.record_failure(redis, login)
        error = AuthError.locked if count >= login_guard.MAX_ATTEMPTS else AuthError.invalid
        return AuthResult(error=error)

    if not user.is_active:
        return AuthResult(error=AuthError.inactive)

    # Successful login: clear counter and opportunistically upgrade the hash.
    await login_guard.reset(redis, login)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        await session.commit()

    return AuthResult(user=user)


async def _apply_scopes(session: AsyncSession, user: User, scopes: list[ScopeIn]) -> None:
    """Replace the user's scopes, dropping references to non-existent companies/projects.

    Validating here (instead of relying on FK errors) keeps the transaction clean and
    prevents an admin typo in a scope id from 500-ing user creation.
    """
    from app.models.company import Company, Project

    company_ids = {s.company_id for s in scopes if s.company_id is not None}
    project_ids = {s.project_id for s in scopes if s.project_id is not None}

    valid_companies: set[int] = set()
    if company_ids:
        rows = await session.execute(select(Company.id).where(Company.id.in_(company_ids)))
        valid_companies = set(rows.scalars().all())
    valid_projects: set[int] = set()
    if project_ids:
        rows = await session.execute(select(Project.id).where(Project.id.in_(project_ids)))
        valid_projects = set(rows.scalars().all())

    result: list[UserScope] = []
    for s in scopes:
        if s.company_id is not None and s.company_id in valid_companies:
            result.append(UserScope(company_id=s.company_id))
        elif s.project_id is not None and s.project_id in valid_projects:
            result.append(UserScope(project_id=s.project_id))
    user.scopes = result


async def create_user(session: AsyncSession, data: UserCreate, *, actor_id: int | None) -> User:
    user = User(
        email=data.email,
        login=data.login,
        password_hash=hash_password(data.password),
        role=data.role,
        is_active=True,
    )
    await _apply_scopes(session, user, data.scopes)
    session.add(user)
    await session.flush()  # assign id
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="user",
        entity_id=user.id,
        diff={"login": data.login, "email": data.email, "role": data.role.value},
    )
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(
    session: AsyncSession, user: User, data: UserUpdate, *, actor_id: int | None
) -> User:
    diff: dict[str, object] = {}
    if data.email is not None and data.email != user.email:
        diff["email"] = {"old": user.email, "new": data.email}
        user.email = data.email
    if data.role is not None and data.role != user.role:
        diff["role"] = {"old": user.role.value, "new": data.role.value}
        user.role = data.role
    if data.is_active is not None and data.is_active != user.is_active:
        diff["is_active"] = {"old": user.is_active, "new": data.is_active}
        user.is_active = data.is_active
    if data.scopes is not None:
        await _apply_scopes(session, user, data.scopes)
        diff["scopes"] = [s.model_dump() for s in data.scopes]

    if diff:
        await record_audit(
            session,
            actor_id=actor_id,
            action="update",
            entity_type="user",
            entity_id=user.id,
            diff=diff,
        )
    await session.commit()
    await session.refresh(user)
    return user


async def set_password(
    session: AsyncSession, user: User, new_password: str, *, actor_id: int | None
) -> None:
    user.password_hash = hash_password(new_password)
    await record_audit(
        session,
        actor_id=actor_id,
        action="set_password",
        entity_type="user",
        entity_id=user.id,
        diff=None,  # never log passwords (SEC-2)
    )
    await session.commit()


def user_in_scope(user: User, *, company_id: int | None, project_id: int | None) -> bool:
    """Return True if ``user`` may access the given company/project.

    Admins always pass. Others must have a matching scope row: a company-scope grant
    covers all projects in that company; a project-scope grant covers that project.
    """
    if user.role == Role.admin:
        return True
    for scope in user.scopes:
        if scope.company_id is not None and scope.company_id == company_id:
            return True
        if scope.project_id is not None and scope.project_id == project_id:
            return True
    return False


async def ensure_admin(
    session: AsyncSession,
    redis: aioredis.Redis | None,  # noqa: ARG001 - kept for signature symmetry
    *,
    email: str,
    login: str,
    password: str,
) -> tuple[User, bool]:
    """Idempotently ensure an admin user exists. Returns (user, created)."""
    existing = await get_user_by_login(session, login)
    if existing is not None:
        return existing, False
    user = User(
        email=email,
        login=login,
        password_hash=hash_password(password),
        role=Role.admin,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await record_audit(
        session,
        actor_id=None,
        action="create",
        entity_type="user",
        entity_id=user.id,
        diff={"login": login, "role": Role.admin.value, "source": "seed"},
    )
    await session.commit()
    await session.refresh(user)
    return user, True
