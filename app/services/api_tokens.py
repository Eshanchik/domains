"""Personal API tokens (SPEC FR-API-1, Phase 2 / T21).

Tokens are high-entropy, so a fast SHA-256 hash is stored (not argon2). The plaintext
is returned once at creation and never again.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.api import ApiToken
from app.models.user import User

TOKEN_PREFIX = "dg_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_token(session: AsyncSession, user: User, name: str) -> tuple[ApiToken, str]:
    """Create a token; returns (row, plaintext). Plaintext is shown only here."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        user_id=user.id,
        name=name,
        token_hash=_hash(plaintext),
        prefix=plaintext[:10],
    )
    session.add(token)
    await session.flush()
    await record_audit(
        session,
        actor_id=user.id,
        action="create",
        entity_type="api_token",
        entity_id=token.id,
        diff={"name": name},
    )
    await session.commit()
    await session.refresh(token)
    return token, plaintext


async def list_for_user(session: AsyncSession, user_id: int) -> list[ApiToken]:
    result = await session.execute(
        select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.id.desc())
    )
    return list(result.scalars().all())


async def revoke(session: AsyncSession, token: ApiToken, *, actor_id: int) -> None:
    token.is_active = False
    await record_audit(
        session,
        actor_id=actor_id,
        action="revoke",
        entity_type="api_token",
        entity_id=token.id,
        diff={"name": token.name},
    )
    await session.commit()


async def resolve_user(session: AsyncSession, plaintext: str) -> User | None:
    """Return the active user owning ``plaintext``, updating last_used_at."""
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None
    result = await session.execute(
        select(ApiToken).where(
            ApiToken.token_hash == _hash(plaintext), ApiToken.is_active.is_(True)
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        return None
    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        return None
    token.last_used_at = datetime.now(UTC)
    await session.commit()
    return user
