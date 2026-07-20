"""Notification channel management, routing resolver, and delivery (SPEC §3.6)."""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import webhook as webhook_channels
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


CHANNEL_TYPES = ("telegram", "slack", "discord", "webhook")


async def create_channel_typed(
    session: AsyncSession,
    *,
    type: str,
    name: str,
    config: dict,
    company_id: int | None,
    project_id: int | None,
    is_default: bool,
    mode: str,
    digest_time: str | None,
    actor_id: int,
) -> NotificationChannel:
    channel = NotificationChannel(
        type=type,
        name=name,
        config_enc=crypto.encrypt(json.dumps(config)),
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
        diff={"name": name, "type": type, "scope": _scope_label(channel)},
    )
    await session.commit()
    await session.refresh(channel)
    return channel


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
    """Backward-compatible Telegram channel creator."""
    return await create_channel_typed(
        session,
        type="telegram",
        name=name,
        config={"chat_id": chat_id},
        company_id=company_id,
        project_id=project_id,
        is_default=is_default,
        mode=mode,
        digest_time=digest_time,
        actor_id=actor_id,
    )


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


def channel_config(channel: NotificationChannel) -> dict:
    if not channel.config_enc:
        return {}
    try:
        return json.loads(crypto.decrypt(channel.config_enc))
    except (crypto.CryptoError, json.JSONDecodeError):
        return {}


def channel_chat_id(channel: NotificationChannel) -> str | None:
    return channel_config(channel).get("chat_id")


def channel_target(channel: NotificationChannel) -> str:
    """Non-secret target label for the UI (chat_id or webhook host)."""
    cfg = channel_config(channel)
    if channel.type == "telegram":
        return cfg.get("chat_id", "") or ""
    url = cfg.get("webhook_url", "") or ""
    # Show only the host to avoid leaking the webhook token.
    if "//" in url:
        return url.split("//", 1)[1].split("/", 1)[0]
    return crypto.mask(url)


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


async def _build_impl(session, channel: NotificationChannel, client=None):
    """Instantiate the channel implementation from its type + config, or None."""
    cfg = channel_config(channel)
    if channel.type == "telegram":
        token = await settings_store.get_secret(session, settings_store.TELEGRAM_BOT_TOKEN)
        chat_id = cfg.get("chat_id")
        if not token or not chat_id:
            return None
        return TelegramChannel(token, chat_id, client=client)

    url = cfg.get("webhook_url")
    if not url:
        return None
    if channel.type == "slack":
        return webhook_channels.SlackChannel(url, client=client)
    if channel.type == "discord":
        return webhook_channels.DiscordChannel(url, client=client)
    if channel.type == "webhook":
        return webhook_channels.GenericWebhookChannel(url, client=client)
    return None


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
    impl = await _build_impl(session, channel, client=client)
    if impl is None:
        _log_delivery(session, channel.id, alert_event_id, "failed", "not configured")
        await session.commit()
        return False

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
