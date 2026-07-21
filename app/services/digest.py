"""Daily digest: per-channel summary of active alerts (SPEC FR-AL-5).

Composed from active AlertEvents for the channel's scoped domains (project → its
domains, company → its domains, global → all). Sent once per day at the channel's
``digest_time`` (Europe/Kyiv). Idempotency uses a Redis SET NX marker per
(channel, date) so a restart or an extra scheduler tick cannot double-send.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.company import Project
from app.models.domain import Domain
from app.models.notification import NotificationChannel

log = logging.getLogger("services.digest")

_SECTIONS = [
    ("expiry", "⏳", "Истекают домены"),
    ("ssl", "🔒", "Истекает SSL"),
    ("vt_malicious", "🚨", "VirusTotal детекты"),
    ("health_down", "🔴", "Health-check недоступны"),
    ("ns_change", "🛡️", "Смена NS"),
]


async def scoped_domain_ids(session: AsyncSession, channel: NotificationChannel) -> set[int] | None:
    """Domain ids in the channel's scope. None means all domains (global channel)."""
    if channel.project_id is not None:
        rows = await session.execute(
            select(Domain.id).where(Domain.project_id == channel.project_id)
        )
        return set(rows.scalars().all())
    if channel.company_id is not None:
        rows = await session.execute(
            select(Domain.id)
            .join(Project, Project.id == Domain.project_id)
            .where(Project.company_id == channel.company_id)
        )
        return set(rows.scalars().all())
    return None  # global


async def compose_digest(session: AsyncSession, channel: NotificationChannel) -> str | None:
    """Build the digest text for a channel, or None if there is nothing to report."""
    scope = await scoped_domain_ids(session, channel)
    stmt = (
        select(AlertEvent, Domain.fqdn, Domain.expiry_date)
        .join(Domain, Domain.id == AlertEvent.domain_id)
        .where(AlertEvent.state == "active")
    )
    if scope is not None:
        if not scope:
            return None
        stmt = stmt.where(AlertEvent.domain_id.in_(scope))
    rows = (await session.execute(stmt)).all()
    if not rows:
        return None

    by_kind: dict[str, list[str]] = {}
    for event, fqdn, expiry in rows:
        p = event.payload_json or {}
        if event.kind == "expiry":
            date = expiry.strftime("%Y-%m-%d") if expiry else "—"
            label = f"{fqdn} — через {p.get('days')} дн. ({date})"
        elif event.kind == "ssl":
            label = f"{fqdn} — через {p.get('days')} дн."
        elif event.kind == "vt_malicious":
            label = f"{fqdn} — {p.get('malicious')} детектов"
        elif event.kind == "health_down":
            label = f"{fqdn} — недоступен"
        elif event.kind == "ns_change":
            label = f"{fqdn} — сменились NS"
        else:
            label = fqdn
        by_kind.setdefault(event.kind, []).append(label)

    lines = [f"📋 DomainGuard — активные алерты ({len(rows)})"]
    for kind, emoji, title in _SECTIONS:
        items = by_kind.get(kind)
        if items:
            lines.append(f"\n{emoji} {title} ({len(items)}):")
            lines.extend(f"  • {item}" for item in items)
    return "\n".join(lines)


def _digest_key(channel_id: int, day: str) -> str:
    return f"digest:{channel_id}:{day}"


async def run_digests(
    session: AsyncSession,
    redis: aioredis.Redis,
    *,
    now_kyiv: datetime,
    send: Callable[[int, str], None],
) -> list[int]:
    """Send digests for channels whose digest_time matches ``now_kyiv`` (once/day)."""
    hhmm = now_kyiv.strftime("%H:%M")
    day = now_kyiv.strftime("%Y%m%d")
    result = await session.execute(
        select(NotificationChannel).where(
            NotificationChannel.is_enabled.is_(True),
            NotificationChannel.mode.in_(["digest", "both"]),
            NotificationChannel.digest_time == hhmm,
        )
    )
    sent: list[int] = []
    for channel in result.scalars().all():
        # Claim the (channel, day) slot atomically; skip if already claimed.
        claimed = await redis.set(_digest_key(channel.id, day), "1", nx=True, ex=2 * 24 * 3600)
        if not claimed:
            continue
        text = await compose_digest(session, channel)
        if text:
            send(channel.id, text)
            sent.append(channel.id)
    return sent
