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
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.notification import NotificationChannel
from app.models.registrar import RegistrarAccount

log = logging.getLogger("services.digest")

# Non-expiry sections keep a flat list; expiry is grouped by urgency (see below).
_SECTIONS = [
    ("ssl", "🔒", "Истекает SSL"),
    ("vt_malicious", "🚨", "VirusTotal детекты"),
    ("health_down", "🔴", "Health-check недоступны"),
    ("ns_change", "🛡️", "Смена NS"),
]


def _expiry_bucket(days: object) -> tuple[int, str, str]:
    """Map days-until-expiry to an urgency bucket (order, emoji, title)."""
    if not isinstance(days, int):
        return (5, "⚪", "срок неизвестен")
    if days < 0:
        return (0, "💀", "просрочены")
    if days <= 7:
        return (1, "🔴", "≤7 дней")
    if days <= 30:
        return (2, "🟠", "8–30 дней")
    if days <= 60:
        return (3, "🟡", "31–60 дней")
    return (4, "⚪", "60+ дней")


async def _scope_name(session: AsyncSession, channel: NotificationChannel) -> str:
    """Human name of the channel's scope for the digest header."""
    if channel.project_id is not None:
        return (
            await session.scalar(select(Project.name).where(Project.id == channel.project_id))
            or "проект"
        )
    if channel.company_id is not None:
        return (
            await session.scalar(select(Company.name).where(Company.id == channel.company_id))
            or "компания"
        )
    return "все домены"


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
    """Build the digest text for a channel, or None if there is nothing to report.

    Only **active** (non-archived) domains are included; expiry alerts are grouped by
    urgency and each line names the registrar account it belongs to.
    """
    scope = await scoped_domain_ids(session, channel)
    stmt = (
        select(AlertEvent, Domain.fqdn, Domain.expiry_date, RegistrarAccount.label)
        .join(Domain, Domain.id == AlertEvent.domain_id)
        .outerjoin(RegistrarAccount, RegistrarAccount.id == Domain.registrar_account_id)
        .where(AlertEvent.state == "active", Domain.is_active.is_(True))
    )
    if scope is not None:
        if not scope:
            return None
        stmt = stmt.where(AlertEvent.domain_id.in_(scope))
    rows = (await session.execute(stmt)).all()
    if not rows:
        return None

    def _acct(label: str | None) -> str:
        return f" · {label}" if label else ""

    # Expiry alerts: (days, bucket, text) for grouped, sorted output.
    expiry: list[tuple[int, tuple[int, str, str], str]] = []
    other: dict[str, list[str]] = {}
    for event, fqdn, exp_date, acct in rows:
        p = event.payload_json or {}
        if event.kind == "expiry":
            days = p.get("days")
            date = exp_date.strftime("%Y-%m-%d") if exp_date else "—"
            sort_key = days if isinstance(days, int) else 9999
            expiry.append(
                (sort_key, _expiry_bucket(days), f"{fqdn} — {days} дн. · {date}{_acct(acct)}")
            )
        elif event.kind == "ssl":
            other.setdefault("ssl", []).append(f"{fqdn} — {p.get('days')} дн.{_acct(acct)}")
        elif event.kind == "vt_malicious":
            other.setdefault("vt_malicious", []).append(
                f"{fqdn} — {p.get('malicious')} детектов{_acct(acct)}"
            )
        elif event.kind == "health_down":
            other.setdefault("health_down", []).append(f"{fqdn} — недоступен{_acct(acct)}")
        elif event.kind == "ns_change":
            other.setdefault("ns_change", []).append(f"{fqdn} — сменились NS{_acct(acct)}")
        else:
            other.setdefault(event.kind, []).append(f"{fqdn}{_acct(acct)}")

    scope_name = await _scope_name(session, channel)
    lines = [f"📋 DomainGuard · {scope_name}", f"Ежедневная сводка — активных алертов: {len(rows)}"]

    if expiry:
        lines.append(f"\n⏳ Истекают домены ({len(expiry)}):")
        expiry.sort(key=lambda t: t[0])
        seen_buckets: dict[int, tuple[str, str]] = {}
        grouped: dict[int, list[str]] = {}
        for _, (order, emoji, title), text in expiry:
            seen_buckets[order] = (emoji, title)
            grouped.setdefault(order, []).append(text)
        for order in sorted(grouped):
            emoji, title = seen_buckets[order]
            lines.append(f"  {emoji} {title}:")
            lines.extend(f"    • {t}" for t in grouped[order])

    for kind, emoji, title in _SECTIONS:
        items = other.get(kind)
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
