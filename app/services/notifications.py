"""Notification channel management, routing resolver, and delivery (SPEC §3.6)."""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import ChannelError, ChannelTransientError
from app.channels.telegram import TelegramChannel
from app.core import crypto
from app.core.audit import record_audit
from app.core.retry import RetryError, with_retry
from app.models.company import Project
from app.models.domain import Domain
from app.models.notification import NotificationChannel, NotificationLog
from app.services import settings_store

log = logging.getLogger("services.notifications")


# --- CRUD --------------------------------------------------------------------


async def list_channels(session: AsyncSession) -> list[NotificationChannel]:
    result = await session.execute(select(NotificationChannel).order_by(NotificationChannel.id))
    return list(result.scalars().all())


async def get_channel(session: AsyncSession, channel_id: int) -> NotificationChannel | None:
    return await session.get(NotificationChannel, channel_id)


async def create_channel(
    session: AsyncSession,
    *,
    name: str,
    chat_id: str,
    company_id: int | None,
    project_id: int | None,
    is_default: bool,
    mode: str,
    digest_time: str | None,
    actor_id: int,
) -> NotificationChannel:
    channel = NotificationChannel(
        type="telegram",
        name=name,
        config_enc=crypto.encrypt(json.dumps({"chat_id": chat_id})),
        company_id=company_id,
        project_id=project_id,
        is_default=is_default,
        mode=mode,
        digest_time=digest_time or None,
        is_enabled=True,
    )
    session.add(channel)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="channel",
        entity_id=channel.id,
        diff={"name": name, "scope": _scope_label(channel)},
    )
    await session.commit()
    await session.refresh(channel)
    return channel


async def delete_channel(
    session: AsyncSession, channel: NotificationChannel, *, actor_id: int
) -> None:
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="channel",
        entity_id=channel.id,
        diff={"name": channel.name},
    )
    await session.delete(channel)
    await session.commit()


def _scope_label(c: NotificationChannel) -> str:
    if c.is_default:
        return "global"
    if c.project_id:
        return f"project:{c.project_id}"
    if c.company_id:
        return f"company:{c.company_id}"
    return "unassigned"


def channel_chat_id(channel: NotificationChannel) -> str | None:
    if not channel.config_enc:
        return None
    try:
        return json.loads(crypto.decrypt(channel.config_enc)).get("chat_id")
    except (crypto.CryptoError, json.JSONDecodeError):
        return None


# --- Routing -----------------------------------------------------------------


def _mode_ok(channel: NotificationChannel, purpose: str | None) -> bool:
    if purpose is None:
        return True
    return channel.mode == "both" or channel.mode == purpose


async def resolve_channels(
    session: AsyncSession, domain: Domain, *, purpose: str | None = None
) -> list[NotificationChannel]:
    """Resolve channels for a domain: project → company → global (first non-empty)."""

    async def _at(**where) -> list[NotificationChannel]:
        stmt = select(NotificationChannel).where(
            NotificationChannel.is_enabled.is_(True),
            *[getattr(NotificationChannel, k) == v for k, v in where.items()],
        )
        rows = list((await session.execute(stmt)).scalars().all())
        return [c for c in rows if _mode_ok(c, purpose)]

    project_channels = await _at(project_id=domain.project_id)
    if project_channels:
        return project_channels

    project = await session.get(Project, domain.project_id)
    if project is not None:
        company_channels = await _at(company_id=project.company_id, project_id=None)
        if company_channels:
            return company_channels

    return await _at(is_default=True)


# --- Delivery ----------------------------------------------------------------


async def send_to_channel(
    session: AsyncSession,
    redis: aioredis.Redis,  # noqa: ARG001 — reserved for a per-bot send limiter
    channel: NotificationChannel,
    text: str,
    *,
    alert_event_id: int | None = None,
    client=None,
) -> bool:
    """Send ``text`` via ``channel`` (with retry) and log the outcome. Returns success."""
    bot_token = await settings_store.get_secret(session, settings_store.TELEGRAM_BOT_TOKEN)
    chat_id = channel_chat_id(channel)
    if not bot_token or not chat_id:
        _log_delivery(session, channel.id, alert_event_id, "failed", "not configured")
        await session.commit()
        return False

    impl = TelegramChannel(bot_token, chat_id, client=client)
    try:
        await with_retry(lambda: impl.send(text), retries=3, exceptions=(ChannelTransientError,))
        _log_delivery(session, channel.id, alert_event_id, "sent", None)
        await session.commit()
        return True
    except (RetryError, ChannelError) as exc:
        log.warning("channel %s delivery failed: %s", channel.id, exc)
        _log_delivery(session, channel.id, alert_event_id, "failed", str(exc))
        await session.commit()
        return False


def _log_delivery(session, channel_id, alert_event_id, status, error) -> None:
    session.add(
        NotificationLog(
            channel_id=channel_id,
            alert_event_id=alert_event_id,
            delivery_status=status,
            error=error,
        )
    )
