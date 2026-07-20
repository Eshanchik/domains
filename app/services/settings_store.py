"""Encrypted key/value settings service (SEC-1/SEC-2).

Values are Fernet-encrypted at rest; reads decrypt on demand. The plaintext is
never returned to templates/logs — use ``get_masked`` for display.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models.setting import Setting

VT_API_KEY = "vt_api_key"
TELEGRAM_BOT_TOKEN = "telegram_bot_token"


async def get_secret(session: AsyncSession, key: str) -> str | None:
    row = await session.get(Setting, key)
    if row is None or not row.value_enc:
        return None
    try:
        return crypto.decrypt(row.value_enc)
    except crypto.CryptoError:
        return None


async def set_secret(session: AsyncSession, key: str, value: str | None) -> None:
    row = await session.get(Setting, key)
    enc = crypto.encrypt(value) if value else None
    if row is None:
        session.add(Setting(key=key, value_enc=enc))
    else:
        row.value_enc = enc
    await session.commit()


async def get_masked(session: AsyncSession, key: str) -> str:
    secret = await get_secret(session, key)
    return crypto.mask(secret)
