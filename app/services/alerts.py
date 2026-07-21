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
from app.models.company import Company, Project
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


async def resolve_event(
    session: AsyncSession, event_id: int, *, now: datetime | None = None
) -> bool:
    """Manually resolve one active alert event by id. Returns True if it was active."""
    ev = await session.get(AlertEvent, event_id)
    if ev is None or ev.state != "active":
        return False
    ev.state = "resolved"
    ev.resolved_at = now or datetime.now(UTC)
    await session.commit()
    return True


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


async def evaluate_dns(
    session: AsyncSession, domain: Domain, *, now: datetime | None = None
) -> list[AlertEvent]:
    """Fire an ns_change alert if the latest DNS snapshot's NS set differs from the
    previous one (SPEC FR-CK-5). Each distinct new NS set fires once; the previous
    ns_change event is resolved."""
    ts = now or datetime.now(UTC)
    from app.models.check_result import CheckResult

    result = await session.execute(
        select(CheckResult.data_json)
        .where(CheckResult.domain_id == domain.id, CheckResult.type == "dns")
        .order_by(CheckResult.checked_at.desc())
        .limit(2)
    )
    snapshots = [row[0] or {} for row in result.all()]
    if len(snapshots) < 2:
        return []  # first snapshot — nothing to compare
    new_ns = sorted(snapshots[0].get("ns") or [])
    old_ns = sorted(snapshots[1].get("ns") or [])
    if not new_ns or new_ns == old_ns:
        return []

    key = f"{domain.id}:ns_change:{'|'.join(new_ns)}"
    event, created = await _ensure_active(
        session,
        domain_id=domain.id,
        kind="ns_change",
        dedupe_key=key,
        severity="high",
        payload={"old_ns": old_ns, "new_ns": new_ns},
        now=ts,
    )
    await _resolve(session, domain_id=domain.id, kind="ns_change", keep_key=key, now=ts)
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


_SEV_LABEL = {"high": "🔴 HIGH", "medium": "🟠 MED", "low": "⚪ LOW"}


def build_message(
    event: AlertEvent,
    domain: Domain,
    *,
    project: str | None = None,
    company: str | None = None,
) -> str:
    """A clear, multi-line alert message that renders in Telegram/Discord/webhook.

    Plain text + emoji (no markdown dialect) so it looks the same in every channel.
    """
    p = event.payload_json or {}
    days = p.get("days")
    threshold = p.get("threshold")
    fqdn = domain.fqdn
    sev = _SEV_LABEL.get(event.severity, event.severity.upper())
    loc = f"\n📁 {project} · {company}" if project else ""

    if event.kind == "expiry":
        date = domain.expiry_date.strftime("%Y-%m-%d") if domain.expiry_date else "—"
        return (
            f"{sev} · истекает домен\n"
            f"🌐 {fqdn}\n"
            f"⏳ через {days} дн. — {date}  (порог ≤{threshold} дн.){loc}"
        )
    if event.kind == "ssl":
        return (
            f"{sev} · истекает SSL-сертификат\n"
            f"🔒 {fqdn}\n"
            f"⏳ через {days} дн.  (порог ≤{threshold} дн.){loc}"
        )
    if event.kind == "vt_malicious":
        return (
            f"{sev} · VirusTotal\n"
            f"🚨 {fqdn} помечен как вредоносный — детектов: {p.get('malicious')}{loc}"
        )
    if event.kind == "health_down":
        return (
            f"{sev} · health-check недоступен\n🔴 {fqdn}  (check #{p.get('healthcheck_id')}){loc}"
        )
    if event.kind == "ns_change":
        new_ns = ", ".join(p.get("new_ns") or []) or "—"
        old_ns = ", ".join(p.get("old_ns") or []) or "—"
        return f"{sev} · сменились NS\n🛡️ {fqdn}\nбыло:  {old_ns}\nстало: {new_ns}{loc}"
    return f"{sev} · {event.kind}\n🌐 {fqdn}{loc}"


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

    project, company = await domain_location(session, domain)
    count = 0
    for event in high:
        text = build_message(event, domain, project=project, company=company)
        for channel in channels:
            send(channel.id, text, event.id)
            count += 1
    return count


async def domain_location(session: AsyncSession, domain: Domain) -> tuple[str | None, str | None]:
    """Return (project_name, company_name) for a domain, for alert context."""
    row = (
        await session.execute(
            select(Project.name, Company.name)
            .join(Company, Company.id == Project.company_id)
            .where(Project.id == domain.project_id)
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


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
    elif check_type == "dns":
        events = await evaluate_dns(session, domain, now=now)
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
