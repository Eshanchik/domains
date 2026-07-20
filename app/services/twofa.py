"""TOTP two-factor authentication (SPEC AUTH-1 / Phase 2, T22).

Secrets are stored encrypted at rest. Enrollment generates a secret and a
provisioning URI (for an authenticator app); 2FA is enabled only after the user
proves possession with a valid code.
"""

from __future__ import annotations

import pyotp
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.audit import record_audit
from app.models.user import User

ISSUER = "DomainGuard"


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, login: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=login, issuer_name=ISSUER)


def verify(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def user_secret(user: User) -> str | None:
    if not user.totp_secret_enc:
        return None
    try:
        return crypto.decrypt(user.totp_secret_enc)
    except crypto.CryptoError:
        return None


async def begin_enrollment(session: AsyncSession, user: User) -> str:
    """Store a fresh (not-yet-enabled) secret and return it for display/QR."""
    secret = generate_secret()
    user.totp_secret_enc = crypto.encrypt(secret)
    user.totp_enabled = False
    await session.commit()
    return secret


async def enable(session: AsyncSession, user: User, code: str) -> bool:
    """Enable 2FA if ``code`` matches the pending secret. Returns success."""
    secret = user_secret(user)
    if not secret or not verify(secret, code):
        return False
    user.totp_enabled = True
    await record_audit(
        session, actor_id=user.id, action="enable_2fa", entity_type="user", entity_id=user.id
    )
    await session.commit()
    return True


async def disable(session: AsyncSession, user: User) -> None:
    user.totp_enabled = False
    user.totp_secret_enc = None
    await record_audit(
        session, actor_id=user.id, action="disable_2fa", entity_type="user", entity_id=user.id
    )
    await session.commit()
