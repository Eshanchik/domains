"""Alert engine: evaluate checks → dedup'd AlertEvents → (instant) notifications.

Default thresholds (SPEC FR-AL-3): expiry 60/30/14/7/1, ssl 30/14/7/3/1. At most one
active event per (domain, kind, threshold) — crossing a tighter threshold fires a new
event, repeated runs at the same band do not (dedup). Severity high (VT malicious,
health down, expiry ≤ 7) is dispatched instantly; the rest wait for the daily digest.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.models.ssl_certificate import SslCertificate
from app.models.vt_result import VtResult
from app.services import notifications as notif

log = logging.getLogger("services.alerts")

EXPIRY_THRESHOLDS = (60, 30, 14, 7, 1)
SSL_THRESHOLDS = (30, 14, 7, 3, 1)


def _days_until(when: datetime, now: datetime) -> int:
    return (when - now).days


async def _active_event(session: AsyncSession, dedupe_key: str) -> AlertEvent | None:
    result = await session.execute(
        select(AlertEvent).where(AlertEvent.dedupe_key == dedupe_key, AlertEvent.state == "active")
    )
    return result.scalar_one_or_none()


async def _ensure_active(
    session: AsyncSession,
    *,
    domain_id: int,
    kind: str,
    dedupe_key: str,
    severity: str,
    payload: dict[str, Any],
    now: datetime,
) -> tuple[AlertEvent, bool]:
    existing = await _active_event(session, dedupe_key)
    if existing is not None:
        return existing, False
    event = AlertEvent(
        domain_id=domain_id,
        kind=kind,
        dedupe_key=dedupe_key,
        severity=severity,
        state="active",
        fired_at=now,
        payload_json=payload,
    )
    session.add(event)
    await session.flush()
    return event, True


async def _resolve(
    session: AsyncSession, *, domain_id: int, kind: str, keep_key: str | None, now: datetime
) -> None:
    result = await session.execute(
        select(AlertEvent).where(
            AlertEvent.domain_id == domain_id,
            AlertEvent.kind == kind,
            AlertEvent.state == "active",
        )
    )
    for ev in result.scalars().all():
        if keep_key is not None and ev.dedupe_key == keep_key:
            continue
        ev.state = "resolved"
        ev.resolved_at = now


async def evaluate_expiry(
    session: AsyncSession, domain: Domain, *, now: datetime | None = None
) -> list[AlertEvent]:
    ts = now or datetime.now(UTC)
    if domain.expiry_date is None:
        await _resolve(session, domain_id=domain.id, kind="expiry", keep_key=None, now=ts)
        return []
    days = _days_until(domain.expiry_date, ts)
    crossed = [t for t in EXPIRY_THRESHOLDS if days <= t]
    if not crossed:
        await _resolve(session, domain_id=domain.id, kind="expiry", keep_key=None, now=ts)
        return []
    t = min(crossed)
    key = f"{domain.id}:expiry:{t}"
    severity = "high" if t <= 7 else "medium"
    event, created = await _ensure_active(
        session,
        domain_id=domain.id,
        kind="expiry",
        dedupe_key=key,
        severity=severity,
        payload={"days": days, "threshold": t},
        now=ts,
    )
    await _resolve(session, domain_id=domain.id, kind="expiry", keep_key=key, now=ts)
    return [event] if created else []


async def evaluate_ssl(
    session: AsyncSession, domain: Domain, valid_to: datetime | None, *, now: datetime | None = None
) -> list[AlertEvent]:
    ts = now or datetime.now(UTC)
    if valid_to is None:
        await _resolve(session, domain_id=domain.id, kind="ssl", keep_key=None, now=ts)
        return []
    days = _days_until(valid_to, ts)
    crossed = [t for t in SSL_THRESHOLDS if days <= t]
    if not crossed:
        await _resolve(session, domain_id=domain.id, kind="ssl", keep_key=None, now=ts)
        return []
    t = min(crossed)
    key = f"{domain.id}:ssl:{t}"
    event, created = await _ensure_active(
        session,
        domain_id=domain.id,
        kind="ssl",
        dedupe_key=key,
        severity="medium",
        payload={"days": days, "threshold": t},
        now=ts,
    )
    await _resolve(session, domain_id=domain.id, kind="ssl", keep_key=key, now=ts)
    return [event] if created else []


async def evaluate_vt(
    session: AsyncSession, domain: Domain, malicious: int, *, now: datetime | None = None
) -> list[AlertEvent]:
    ts = now or datetime.now(UTC)
    if malicious < 1:
        await _resolve(session, domain_id=domain.id, kind="vt_malicious", keep_key=None, now=ts)
        return []
    key = f"{domain.id}:vt"
    event, created = await _ensure_active(
        session,
        domain_id=domain.id,
        kind="vt_malicious",
        dedupe_key=key,
        severity="high",
        payload={"malicious": malicious},
        now=ts,
    )
    return [event] if created else []


async def evaluate_health(
    session: AsyncSession,
    domain_id: int,
    healthcheck_id: int,
    transition: str | None,
    *,
    now: datetime | None = None,
) -> list[AlertEvent]:
    ts = now or datetime.now(UTC)
    key = f"{domain_id}:health:{healthcheck_id}"
    if transition == "down":
        event, created = await _ensure_active(
            session,
            domain_id=domain_id,
            kind="health_down",
            dedupe_key=key,
            severity="high",
            payload={"healthcheck_id": healthcheck_id},
            now=ts,
        )
        return [event] if created else []
    if transition == "recovered":
        await _resolve(session, domain_id=domain_id, kind="health_down", keep_key=None, now=ts)
    return []


# --- Message templates (RU) --------------------------------------------------


def build_message(event: AlertEvent, domain: Domain) -> str:
    p = event.payload_json or {}
    days = p.get("days")
    fqdn = domain.fqdn
    if event.kind == "expiry":
        return f"⚠️ Домен {fqdn} истекает через {days} дн. (порог {p.get('threshold')})."
    if event.kind == "ssl":
        return f"🔒 SSL {fqdn} истекает через {days} дн. (порог {p.get('threshold')})."
    if event.kind == "vt_malicious":
        return f"🚨 VirusTotal: {fqdn} — вредоносный ({p.get('malicious')} детектов)."
    if event.kind == "health_down":
        return f"🔴 Health-check {fqdn} недоступен (check #{p.get('healthcheck_id')})."
    return f"Событие по домену {fqdn}: {event.kind}"


async def dispatch_instant(
    session: AsyncSession,
    redis: aioredis.Redis,
    domain: Domain,
    events: list[AlertEvent],
    *,
    send: Callable[[int, str, int], None] | None = None,
) -> int:
    """Deliver high-severity events immediately to resolved channels. Returns count sent."""
    high = [e for e in events if e.severity == "high"]
    if not high:
        return 0
    channels = await notif.resolve_channels(session, domain, purpose="instant")
    if not channels:
        return 0
    if send is None:
        from app.workers.checks import send_notification

        send = lambda cid, text, eid: send_notification.send(cid, text, eid)  # noqa: E731

    count = 0
    for event in high:
        text = build_message(event, domain)
        for channel in channels:
            send(channel.id, text, event.id)
            count += 1
    return count


async def _latest_ssl_valid_to(session: AsyncSession, domain_id: int) -> datetime | None:
    latest = (
        select(func.max(SslCertificate.checked_at))
        .where(SslCertificate.domain_id == domain_id)
        .scalar_subquery()
    )
    result = await session.execute(
        select(func.min(SslCertificate.valid_to)).where(
            SslCertificate.domain_id == domain_id,
            SslCertificate.checked_at == latest,
            SslCertificate.valid_to.is_not(None),
        )
    )
    return result.scalar_one_or_none()


async def _latest_vt_malicious(session: AsyncSession, domain_id: int) -> int:
    result = await session.execute(
        select(VtResult.malicious)
        .where(VtResult.domain_id == domain_id)
        .order_by(VtResult.checked_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none() or 0


async def evaluate_after_check(
    session: AsyncSession,
    redis: aioredis.Redis,
    domain_id: int,
    check_type: str,
    *,
    now: datetime | None = None,
    send: Callable[[int, str, int], None] | None = None,
) -> list[AlertEvent]:
    """Run the relevant evaluator after a check and dispatch instant alerts."""
    domain = await session.get(Domain, domain_id)
    if domain is None:
        return []
    if check_type == "rdap":
        events = await evaluate_expiry(session, domain, now=now)
    elif check_type == "ssl":
        valid_to = await _latest_ssl_valid_to(session, domain_id)
        events = await evaluate_ssl(session, domain, valid_to, now=now)
    elif check_type == "vt":
        malicious = await _latest_vt_malicious(session, domain_id)
        events = await evaluate_vt(session, domain, malicious, now=now)
    else:
        return []
    await session.commit()
    await dispatch_instant(session, redis, domain, events, send=send)
    return events


async def evaluate_after_healthcheck(
    session: AsyncSession,
    redis: aioredis.Redis,
    domain_id: int,
    healthcheck_id: int,
    transition: str | None,
    *,
    now: datetime | None = None,
    send: Callable[[int, str, int], None] | None = None,
) -> list[AlertEvent]:
    events = await evaluate_health(session, domain_id, healthcheck_id, transition, now=now)
    await session.commit()
    domain = await session.get(Domain, domain_id)
    if domain is not None:
        await dispatch_instant(session, redis, domain, events, send=send)
    return events
