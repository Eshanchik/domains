"""Tracking domains monitor (T99): the board behind «Трекинг» and the status page.

A *tracking domain* is an active domain tagged ``tracking`` — the affiliate/click
tracker domains whose job is to answer (usually with a redirect) on a click URL. Their
liveness comes from the domain's health-checks; the board adds uptime, "down since",
recent history, and the flags that break a tracker (VirusTotal detections, SSL, DNS,
expiry).

The same board feeds the internal tab (scoped by the user's projects) and the
read-only status page for employees who sign in with a company Google account
(scoped by the access rule matching their e-mail domain).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, not_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.fqdn import normalize_fqdn
from app.models.alert import AlertEvent
from app.models.check_result import CheckResult
from app.models.company import Company, Project, Tag
from app.models.domain import Domain, DomainTag
from app.models.healthcheck import HealthCheck, HealthCheckResult
from app.models.vt_result import VtResult
from app.schemas.healthcheck import HealthCheckCreate
from app.services import companies as companies_svc
from app.services import healthchecks as healthchecks_svc
from app.services import settings_store

KYIV = ZoneInfo("Europe/Kyiv")
TRACKING_TAG = "tracking"
SPARK_LEN = 24
DEFAULT_TEMPLATE = "https://{fqdn}/"
STATUS_ORDER = {"down": 0, "degraded": 1, "stale": 2, "unknown": 3, "nocheck": 4, "up": 5}
STATUS_LABELS = {
    "up": "работает",
    "down": "лежит",
    "degraded": "сбоит",
    "stale": "нет свежих проверок",
    "unknown": "ещё не проверен",
    "nocheck": "нет проверки",
}
EDGE_WINDOW = timedelta(days=30)  # "down/up since" looks this far back (bounded scan)


@dataclass
class SparkPoint:
    ok: bool
    at_label: str
    detail: str


@dataclass
class TrackRow:
    domain_id: int
    fqdn: str
    project: str
    company: str
    status: str
    status_label: str
    since_label: str = ""
    url: str | None = None
    checks: int = 0
    uptime_24h: float | None = None
    uptime_7d: float | None = None
    spark: list[SparkPoint] = field(default_factory=list)
    last_code: int | None = None
    last_latency_ms: int | None = None
    last_reason: str = ""
    last_checked_label: str = ""
    ssl: tuple[str, str] | None = None  # (label, tone)
    vt_malicious: int | None = None
    dns_status: str | None = None
    expiry_days: int | None = None
    alerts: int = 0
    down_since: datetime | None = None


@dataclass
class TrackingBoard:
    rows: list[TrackRow]
    totals: dict[str, int]
    generated_label: str


# --- helpers -------------------------------------------------------------------------


def _kyiv(dt: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KYIV).strftime(fmt)


def duration_label(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {rem // 60} мин"
    return f"{max(1, rem // 60)} мин"


def humanize_error(error: str | None, status_code: int | None) -> str:
    """Turn a raw health-check failure into something a non-engineer understands."""
    err = (error or "").strip()
    low = err.lower()
    if not err:
        return f"HTTP {status_code}" if status_code else ""
    if low.startswith("unexpected status"):
        return f"неожиданный ответ HTTP {status_code}" if status_code else err
    if "timeout" in low or "timed out" in low:
        return "таймаут — сервер не ответил вовремя"
    if (
        low in ("request failed:", "request failed")
        or "connecterror" in low
        or "name or service not known" in low
        or "nodename nor servname" in low
        or "all connection attempts failed" in low
        or "temporary failure in name resolution" in low
    ):
        return "нет соединения — домен не резолвится или хост недоступен"
    if "ssl" in low or "certificate" in low:
        return "ошибка SSL/TLS при подключении"
    if "location pattern mismatch" in low:
        return "редирект ведёт не туда (не совпал шаблон Location)"
    if "body substring" in low:
        return "в ответе нет ожидаемого текста"
    if low.startswith("blocked"):
        return "адрес заблокирован защитой (внутренний/приватный IP)"
    return err[:160]


def _pct(ok: int, total: int) -> float | None:
    return round(100.0 * ok / total, 1) if total else None


# --- membership ---------------------------------------------------------------------


def _tracking_ids():
    """Subquery of domain ids carrying the tracking tag (case-insensitive)."""
    return (
        select(DomainTag.domain_id)
        .join(Tag, Tag.id == DomainTag.tag_id)
        .where(func.lower(Tag.name) == TRACKING_TAG)
    )


async def _tracking_domains(
    session: AsyncSession,
    *,
    allowed: set[int] | None,
    company_id: int | None,
    project_id: int | None,
    q: str | None,
) -> list[tuple[Domain, str, str]]:
    stmt = (
        select(Domain, Project.name, Company.name)
        .join(Project, Project.id == Domain.project_id)
        .join(Company, Company.id == Project.company_id)
        .where(Domain.id.in_(_tracking_ids()), Domain.is_active.is_(True))
        .order_by(Domain.fqdn)
    )
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(Domain.project_id.in_(allowed))
    if company_id is not None:
        stmt = stmt.where(Company.id == company_id)
    if project_id is not None:
        stmt = stmt.where(Project.id == project_id)
    if q:
        stmt = stmt.where(Domain.fqdn.ilike(f"%{q.strip().lower()}%"))
    return [(d, p, c) for d, p, c in (await session.execute(stmt)).all()]


# --- board ---------------------------------------------------------------------------


def _is_stale(hc: HealthCheck, ts: datetime) -> bool:
    """No result for 3 intervals (min. 1 h): the check stopped running (scheduler/worker
    down, egress lost) — its last state can no longer be trusted."""
    if hc.last_checked_at is None:
        return False
    grace = timedelta(minutes=max(3 * (hc.interval_min or 15), 60))
    return hc.last_checked_at < ts - grace


def _check_status(hc: HealthCheck, ts: datetime) -> str:
    if hc.state == "down":
        return "down"
    if hc.last_checked_at is None:
        return "unknown"
    if _is_stale(hc, ts):
        return "stale"
    if hc.consecutive_failures > 0:
        return "degraded"
    return "up"


async def build_board(
    session: AsyncSession,
    *,
    allowed: set[int] | None,
    company_id: int | None = None,
    project_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    now: datetime | None = None,
) -> TrackingBoard:
    from app.services.domains import ssl_status_map

    ts = now or datetime.now(UTC)
    doms = await _tracking_domains(
        session, allowed=allowed, company_id=company_id, project_id=project_id, q=q
    )
    ids = [d.id for d, _, _ in doms]
    checks: dict[int, list[HealthCheck]] = {i: [] for i in ids}
    if ids:
        for hc in (
            await session.execute(
                select(HealthCheck)
                .where(HealthCheck.domain_id.in_(ids), HealthCheck.is_enabled.is_(True))
                .order_by(HealthCheck.id)
            )
        ).scalars():
            checks[hc.domain_id].append(hc)
    check_ids = [hc.id for lst in checks.values() for hc in lst]

    window = await _window_stats(session, check_ids, ts)
    edges = await _edges(session, check_ids, ts)
    primary = {
        did: min(lst, key=lambda h: (STATUS_ORDER[_check_status(h, ts)], h.id))
        for did, lst in checks.items()
        if lst
    }
    history = await _history(session, [h.id for h in primary.values()], ts)
    ssl = await ssl_status_map(session, ids, now=ts) if ids else {}
    vt = await _latest_vt(session, ids)
    dns = await _latest_dns(session, ids, ts)
    alerts = await _active_alerts(session, ids)

    rows: list[TrackRow] = []
    for d, project, company in doms:
        lst = checks[d.id]
        row = TrackRow(
            domain_id=d.id,
            fqdn=d.fqdn,
            project=project,
            company=company,
            status="nocheck",
            status_label=STATUS_LABELS["nocheck"],
            checks=len(lst),
            ssl=ssl.get(d.id),
            vt_malicious=vt.get(d.id),
            dns_status=dns.get(d.id),
            expiry_days=(d.expiry_date - ts).days if d.expiry_date else None,
            alerts=alerts.get(d.id, 0),
        )
        if lst:
            hc = primary[d.id]
            row.status = _check_status(hc, ts)
            row.status_label = STATUS_LABELS[row.status]
            row.url = hc.url
            ok24 = sum(window.get(h.id, (0, 0, 0, 0))[1] for h in lst)
            n24 = sum(window.get(h.id, (0, 0, 0, 0))[0] for h in lst)
            ok7 = sum(window.get(h.id, (0, 0, 0, 0))[3] for h in lst)
            n7 = sum(window.get(h.id, (0, 0, 0, 0))[2] for h in lst)
            row.uptime_24h, row.uptime_7d = _pct(ok24, n24), _pct(ok7, n7)
            points = history.get(hc.id, [])
            row.spark = [
                SparkPoint(
                    ok,
                    _kyiv(at),
                    f"HTTP {code}" if ok else humanize_error(err, code) or "сбой",
                )
                for ok, code, err, at, _latency in reversed(points)
            ]
            if points:
                ok, code, err, at, latency = points[0]
                row.last_code, row.last_latency_ms = code, latency
                row.last_reason = "" if ok else humanize_error(err, code)
                row.last_checked_label = _kyiv(at)
            last_ok, last_fail, first_at = edges.get(hc.id, (None, None, None))
            if row.status == "down":
                start = last_ok or first_at
                if start is not None:
                    row.down_since = start
                    prefix = "" if last_ok else "≥ "
                    row.since_label = f"лежит {prefix}{duration_label(ts - start)}"
            elif row.status in ("up", "degraded"):
                start = last_fail or first_at
                if start is not None and row.status == "up":
                    prefix = "" if last_fail else "≥ "
                    row.since_label = f"работает {prefix}{duration_label(ts - start)}"
                elif row.status == "degraded":
                    row.since_label = f"сбоев подряд: {hc.consecutive_failures}"
            elif row.status == "stale" and hc.last_checked_at is not None:
                row.since_label = f"последняя — {duration_label(ts - hc.last_checked_at)} назад"
        rows.append(row)

    # Totals describe the whole (scoped) fleet — computed before the status filter so the
    # tiles keep showing e.g. "✔ 15 · ✖ 4" while the table shows only the down ones.
    totals = dict.fromkeys(STATUS_ORDER, 0)
    for r in rows:
        totals[r.status] += 1
    totals["total"] = len(rows)
    totals["vt"] = sum(1 for r in rows if r.vt_malicious)
    totals["expiring"] = sum(1 for r in rows if r.expiry_days is not None and r.expiry_days <= 30)
    if status in STATUS_ORDER:
        rows = [r for r in rows if r.status == status]
    rows.sort(
        key=lambda r: (
            STATUS_ORDER[r.status],
            r.down_since or ts,  # longest-down first
            r.fqdn,
        )
    )
    return TrackingBoard(rows, totals, _kyiv(ts, "%d.%m.%Y %H:%M"))


async def _window_stats(
    session: AsyncSession, check_ids: list[int], ts: datetime
) -> dict[int, tuple[int, int, int, int]]:
    """check id → (results 24h, ok 24h, results 7d, ok 7d)."""
    if not check_ids:
        return {}
    r = HealthCheckResult
    since24, since7 = ts - timedelta(hours=24), ts - timedelta(days=7)
    rows = await session.execute(
        select(
            r.healthcheck_id,
            func.count().filter(r.checked_at >= since24),
            func.count().filter(and_(r.ok.is_(True), r.checked_at >= since24)),
            func.count(),
            func.count().filter(r.ok.is_(True)),
        )
        .where(r.healthcheck_id.in_(check_ids), r.checked_at >= since7, r.checked_at <= ts)
        .group_by(r.healthcheck_id)
    )
    return {hid: (n24, ok24, n7, ok7) for hid, n24, ok24, n7, ok7 in rows.all()}


async def _edges(
    session: AsyncSession, check_ids: list[int], ts: datetime
) -> dict[int, tuple[datetime | None, datetime | None, datetime | None]]:
    """check id → (last ok at, last failure at, first result at) within EDGE_WINDOW.

    Bounded so the 60-second auto-refresh never scans a year of history; an edge older
    than the window shows as «≥ 30 д»."""
    if not check_ids:
        return {}
    r = HealthCheckResult
    rows = await session.execute(
        select(
            r.healthcheck_id,
            func.max(r.checked_at).filter(r.ok.is_(True)),
            func.max(r.checked_at).filter(not_(r.ok)),
            func.min(r.checked_at),
        )
        .where(
            r.healthcheck_id.in_(check_ids),
            r.checked_at >= ts - EDGE_WINDOW,
            r.checked_at <= ts,
        )
        .group_by(r.healthcheck_id)
    )
    return {hid: (last_ok, last_fail, first) for hid, last_ok, last_fail, first in rows.all()}


async def _history(
    session: AsyncSession, check_ids: list[int], ts: datetime
) -> dict[int, list[tuple]]:
    """check id → newest-first list of (ok, status_code, error, checked_at, latency)."""
    if not check_ids:
        return {}
    r = HealthCheckResult
    ranked = (
        select(
            r.healthcheck_id,
            r.ok,
            r.status_code,
            r.error,
            r.checked_at,
            r.latency_ms,
            func.row_number()
            .over(partition_by=r.healthcheck_id, order_by=(r.checked_at.desc(), r.id.desc()))
            .label("rn"),
        )
        .where(r.healthcheck_id.in_(check_ids), r.checked_at >= ts - timedelta(days=30))
        .subquery()
    )
    out: dict[int, list[tuple]] = {}
    rows = await session.execute(
        select(
            ranked.c.healthcheck_id,
            ranked.c.ok,
            ranked.c.status_code,
            ranked.c.error,
            ranked.c.checked_at,
            ranked.c.latency_ms,
        )
        .where(ranked.c.rn <= SPARK_LEN)
        .order_by(ranked.c.healthcheck_id, ranked.c.rn)
    )
    for hid, ok, code, err, at, latency in rows.all():
        out.setdefault(hid, []).append((ok, code, err, at, latency))
    return out


async def _latest_vt(session: AsyncSession, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    ranked = (
        select(
            VtResult.domain_id,
            VtResult.malicious,
            func.row_number()
            .over(
                partition_by=VtResult.domain_id,
                order_by=(VtResult.checked_at.desc(), VtResult.id.desc()),
            )
            .label("rn"),
        )
        .where(VtResult.domain_id.in_(ids))
        .subquery()
    )
    rows = await session.execute(
        select(ranked.c.domain_id, ranked.c.malicious).where(ranked.c.rn == 1)
    )
    return dict(rows.all())


async def _latest_dns(session: AsyncSession, ids: list[int], ts: datetime) -> dict[int, str]:
    if not ids:
        return {}
    ranked = (
        select(
            CheckResult.domain_id,
            CheckResult.status,
            func.row_number()
            .over(
                partition_by=CheckResult.domain_id,
                order_by=(CheckResult.checked_at.desc(), CheckResult.id.desc()),
            )
            .label("rn"),
        )
        .where(
            CheckResult.domain_id.in_(ids),
            CheckResult.type == "dns",
            CheckResult.checked_at >= ts - timedelta(days=3),  # prunes old partitions
        )
        .subquery()
    )
    rows = await session.execute(
        select(ranked.c.domain_id, ranked.c.status).where(ranked.c.rn == 1)
    )
    return dict(rows.all())


async def _active_alerts(session: AsyncSession, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = await session.execute(
        select(AlertEvent.domain_id, func.count())
        .where(AlertEvent.domain_id.in_(ids), AlertEvent.state == "active")
        .group_by(AlertEvent.domain_id)
    )
    return dict(rows.all())


# --- management -----------------------------------------------------------------------


async def suggest_template(session: AsyncSession) -> str:
    """The most common health-check URL among tracking domains, with the host replaced
    by ``{fqdn}`` — e.g. ``https://{fqdn}/click?pid=1&offer_id=625``."""
    rows = await session.execute(
        select(HealthCheck.url, Domain.fqdn)
        .join(Domain, Domain.id == HealthCheck.domain_id)
        .join(DomainTag, DomainTag.domain_id == Domain.id)
        .join(Tag, Tag.id == DomainTag.tag_id)
        .where(func.lower(Tag.name) == TRACKING_TAG)
    )
    counts: dict[str, int] = {}
    for url, fqdn in rows.all():
        tpl = url.replace(fqdn, "{fqdn}", 1)
        if "{fqdn}" in tpl:
            counts[tpl] = counts.get(tpl, 0) + 1
    return max(counts, key=counts.get) if counts else DEFAULT_TEMPLATE


@dataclass
class AddReport:
    added: list[str] = field(default_factory=list)  # newly tagged
    already: list[str] = field(default_factory=list)  # were tracking already
    checks_created: int = 0
    missing: list[str] = field(default_factory=list)  # not in the registry
    archived: list[str] = field(default_factory=list)  # in the registry but archived
    forbidden: list[str] = field(default_factory=list)  # outside the user's scope
    invalid: list[str] = field(default_factory=list)  # not a domain


class TrackingError(ValueError):
    """User-facing (Russian) validation error."""


async def add_to_tracking(
    session: AsyncSession,
    *,
    fqdns: list[str],
    allowed: set[int] | None,
    url_template: str,
    expected_statuses: str,
    follow_redirects: bool,
    actor_id: int,
) -> AddReport:
    """Tag registry domains ``tracking`` and give each a click health-check from the
    template (skipped if the domain already has a check with that exact URL).

    Idempotent: re-adding reports «уже в трекинге» and creates nothing new.
    """
    from app.core import net_guard

    url_template = (url_template or "").strip()
    if "{fqdn}" not in url_template:
        raise TrackingError("В шаблоне URL должен быть {fqdn}, например https://{fqdn}/click")
    try:  # validate once up front so a bad template never half-applies
        net_guard.validate_scheme(url_template.replace("{fqdn}", "example.com"))
    except net_guard.UnsafeUrlError as exc:
        raise TrackingError(f"Недопустимый URL проверки: {exc}") from exc
    template = HealthCheckCreate(
        url=url_template,
        expected_statuses=(expected_statuses or "200-399").strip(),
        follow_redirects=follow_redirects,
    )
    report = AddReport()
    # Reuse an existing tag whatever its case ("Tracking"), so we never create a twin.
    tag = await session.scalar(
        select(Tag).where(func.lower(Tag.name) == TRACKING_TAG).order_by(Tag.id).limit(1)
    ) or await companies_svc.get_or_create_tag(session, TRACKING_TAG)
    seen: set[str] = set()
    for raw in fqdns:
        raw = raw.strip()
        if not raw:
            continue
        try:
            fqdn = normalize_fqdn(raw).fqdn
        except ValueError:
            report.invalid.append(raw)
            continue
        if fqdn in seen:
            continue
        seen.add(fqdn)
        domain = await session.scalar(select(Domain).where(Domain.fqdn == fqdn))
        if domain is None:
            report.missing.append(fqdn)
            continue
        if not domain.is_active:
            report.archived.append(fqdn)
            continue
        if allowed is not None and domain.project_id not in allowed:
            report.forbidden.append(fqdn)
            continue
        if any(t.name.lower() == TRACKING_TAG for t in domain.tags):
            report.already.append(fqdn)
        else:
            domain.tags = [*domain.tags, tag]
            report.added.append(fqdn)
        url = url_template.replace("{fqdn}", domain.fqdn)
        exists = await session.scalar(
            select(HealthCheck.id).where(HealthCheck.domain_id == domain.id, HealthCheck.url == url)
        )
        if exists is None:
            try:
                await healthchecks_svc.create(
                    session,
                    domain.id,
                    template.model_copy(update={"url": url}),
                    actor_id=actor_id,
                )
            except healthchecks_svc.InvalidHealthCheckUrl as exc:
                raise TrackingError(f"Недопустимый URL проверки: {exc}") from exc
            report.checks_created += 1
    if report.added:
        await record_audit(
            session,
            actor_id=actor_id,
            action="tracking_add",
            entity_type="domain",
            diff={"domains": report.added, "url_template": url_template},
        )
    await session.commit()
    return report


async def remove_from_tracking(
    session: AsyncSession, domain_id: int, *, allowed: set[int] | None, actor_id: int
) -> bool:
    """Drop the ``tracking`` tag (the domain's health-checks stay — edit them on the
    domain card)."""
    domain = await session.get(Domain, domain_id)
    if domain is None or (allowed is not None and domain.project_id not in allowed):
        return False
    kept = [t for t in domain.tags if t.name.lower() != TRACKING_TAG]
    if len(kept) == len(domain.tags):
        return False
    domain.tags = kept
    await record_audit(
        session,
        actor_id=actor_id,
        action="tracking_remove",
        entity_type="domain",
        entity_id=domain.id,
        diff={"fqdn": domain.fqdn},
    )
    await session.commit()
    return True


async def recheck(
    session: AsyncSession,
    domain_id: int,
    *,
    allowed: set[int] | None,
    actor_id: int,
    now: datetime | None = None,
) -> int:
    """Pull the domain's health-checks to the front of the schedule (the scheduler runs
    them on its next tick). Returns how many checks were scheduled."""
    domain = await session.get(Domain, domain_id)
    if domain is None or (allowed is not None and domain.project_id not in allowed):
        return 0
    result = await session.execute(
        update(HealthCheck)
        .where(HealthCheck.domain_id == domain_id, HealthCheck.is_enabled.is_(True))
        .values(next_check_at=now or datetime.now(UTC))
    )
    await record_audit(
        session,
        actor_id=actor_id,
        action="tracking_recheck",
        entity_type="domain",
        entity_id=domain_id,
    )
    await session.commit()
    return result.rowcount or 0


# --- status page access ---------------------------------------------------------------

STATUS_ACCESS_KEY = "status_access"
# pg_advisory_xact_lock key serializing read-modify-write of the rules JSON (so two
# admins can't silently undo each other's revocation).
_ACCESS_LOCK_ID = 0x44475354  # "DGST"


@dataclass(frozen=True)
class AccessRule:
    email_domain: str  # e.g. "adera.agency"
    company_id: int | None  # None = tracking domains of every company
    # When this rule (re)started: sessions issued before it are void, so deleting and
    # re-adding a rule never resurrects old sessions.
    since: str | None = field(default=None, compare=False)

    def accepts_session(self, issued_at: str | None) -> bool:
        return self.since is None or (issued_at is not None and issued_at >= self.since)


async def get_access_rules(session: AsyncSession) -> list[AccessRule]:
    raw = await settings_store.get_secret(session, STATUS_ACCESS_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [
        AccessRule(str(r["email_domain"]).lower(), r.get("company_id"), r.get("since"))
        for r in data
        if isinstance(r, dict) and r.get("email_domain")
    ]


def stamp() -> str:
    """Sortable UTC timestamp for rule generations and session issue times."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


async def _lock_rules(session: AsyncSession) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(_ACCESS_LOCK_ID)))


async def _save_rules(session: AsyncSession, rules: list[AccessRule]) -> None:
    payload = json.dumps(
        [
            {"email_domain": r.email_domain, "company_id": r.company_id, "since": r.since}
            for r in rules
        ]
    )
    await settings_store.set_secret(session, STATUS_ACCESS_KEY, payload)  # commits → unlocks


def normalize_email_domain(value: str) -> str:
    v = (value or "").strip().lower().lstrip("@")
    if not v or "." not in v or any(c in v for c in " /@:"):
        raise TrackingError("Укажите домен почты, например adera.agency")
    return v


async def add_access_rule(
    session: AsyncSession, *, email_domain: str, company_id: int | None, actor_id: int
) -> None:
    domain = normalize_email_domain(email_domain)
    if company_id is not None and await session.get(Company, company_id) is None:
        raise TrackingError("Компания не найдена.")
    await _lock_rules(session)
    rules = [r for r in await get_access_rules(session) if r.email_domain != domain]
    rules.append(AccessRule(domain, company_id, stamp()))
    await _save_rules(session, rules)
    await record_audit(
        session,
        actor_id=actor_id,
        action="status_access_set",
        entity_type="setting",
        entity_id=STATUS_ACCESS_KEY,
        diff={"email_domain": domain, "company_id": company_id},
    )
    await session.commit()


async def delete_access_rule(session: AsyncSession, *, email_domain: str, actor_id: int) -> None:
    domain = (email_domain or "").strip().lower()
    await _lock_rules(session)
    rules = await get_access_rules(session)
    await _save_rules(session, [r for r in rules if r.email_domain != domain])
    await record_audit(
        session,
        actor_id=actor_id,
        action="status_access_delete",
        entity_type="setting",
        entity_id=STATUS_ACCESS_KEY,
        diff={"email_domain": domain},
    )
    await session.commit()


def match_access(rules: list[AccessRule], email: str, hd: str | None) -> AccessRule | None:
    """The rule granting this Google identity access, or None.

    Both the verified e-mail's domain AND Google's ``hd`` (hosted-domain) claim must
    equal the rule's domain: ``hd`` is only issued for accounts managed by that Google
    Workspace, so a personal Google account registered on a company address (which
    survives the employee leaving) is refused.
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1]
    for rule in rules:
        if rule.email_domain == domain and (hd or "").lower() == domain:
            return rule
    return None


async def scope_for_rule(session: AsyncSession, rule: AccessRule) -> set[int] | None:
    """Project ids a status viewer may see (None = all companies)."""
    if rule.company_id is None:
        return None
    rows = await session.execute(select(Project.id).where(Project.company_id == rule.company_id))
    return set(rows.scalars().all())
