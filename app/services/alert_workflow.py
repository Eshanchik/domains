"""Alert workflow (T97): owners, "taken into work", comments with @mentions, timeline,
and the detailed alert card.

* **Auto-assignment** — a freshly fired event gets an owner: carried over from the
  event it escalated from (e.g. expiry 30 → 7 days keeps the same person), otherwise
  resolved by routing rules "kind → person" (:func:`app.services.people.route_for`).
* **Assign / ack / comment** — manual actions from the card. Assigning and commenting
  can post to the domain's channels with a real ping (Discord ``<@id>``, Telegram
  ``@username``). Channels receive text; the Discord channel only pings ids listed in
  ``allowed_mentions`` derived from that text.
* **Timeline** — :class:`AlertActivity` rows plus synthesized "fired"/"closed"
  entries, so the card shows the whole story of an alert.

Delivery from the web is synchronous (the api has egress): callers pass ``deliver``,
an ``async (channel, text) -> bool`` — tests inject a fake.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.models.alert import AlertActivity, AlertEvent
from app.models.check_result import CheckResult
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.healthcheck import HealthCheck, HealthCheckResult
from app.models.notification import NotificationChannel, NotificationLog
from app.models.person import Person
from app.models.registrar import Registrar, RegistrarAccount
from app.models.ssl_certificate import SslCertificate
from app.models.user import User
from app.models.vt_result import VtResult
from app.services import notifications as notif
from app.services import people

KYIV = ZoneInfo("Europe/Kyiv")

Deliver = Callable[[NotificationChannel, str], Awaitable[bool]]

KIND_LABELS = {
    "expiry": "Истекает домен",
    "ssl": "Истекает SSL-сертификат",
    "vt_malicious": "VirusTotal: домен помечен",
    "health_down": "Health-check недоступен",
    "ns_change": "Сменились NS",
}
SEVERITY_LABELS = {"high": "Критично", "medium": "Внимание", "low": "К сведению"}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def severity_label(severity: str) -> str:
    return SEVERITY_LABELS.get(severity, severity)


def alert_url(event_id: int) -> str | None:
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}/alerts/{event_id}" if base else None


def _kyiv(dt: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KYIV).strftime(fmt)


# --- activity ------------------------------------------------------------------


def record_activity(
    session: AsyncSession,
    event_id: int,
    kind: str,
    *,
    actor_id: int | None = None,
    body: str | None = None,
    data: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> AlertActivity:
    """Add a timeline entry. Does not commit — the caller owns the transaction."""
    entry = AlertActivity(
        alert_event_id=event_id,
        kind=kind,
        actor_user_id=actor_id,
        body=body,
        data_json=data,
    )
    if at is not None:
        entry.at = at
    session.add(entry)
    return entry


# --- auto-assignment -----------------------------------------------------------


async def auto_assign(session: AsyncSession, domain: Domain, events: list[AlertEvent]) -> None:
    """Give each freshly fired event an owner. Does not commit.

    1. Carry-over: if this event replaced a tighter-threshold predecessor resolved in
       the same evaluation (same timestamp) and that one had an owner, keep the owner —
       a person already handling "expires in 30 days" keeps "expires in 7 days".
    2. Otherwise the routing rule "kind → person" for the domain's scope.
    """
    for ev in events:
        if ev.assignee_person_id is not None:
            continue
        await session.flush()  # make the predecessor's resolved_at visible
        prev = await session.scalar(
            select(AlertEvent)
            .join(Person, Person.id == AlertEvent.assignee_person_id)
            .where(
                AlertEvent.domain_id == domain.id,
                AlertEvent.kind == ev.kind,
                AlertEvent.id != ev.id,
                AlertEvent.resolved_at == ev.fired_at,
                Person.is_active.is_(True),  # a disabled owner is re-routed, not carried
            )
            .order_by(AlertEvent.id.desc())
            .limit(1)
        )
        if prev is not None:
            ev.assignee_person_id = prev.assignee_person_id
            via = "carry"
        else:
            person = await people.route_for(session, domain, ev.kind)
            if person is None:
                continue
            ev.assignee_person_id = person.id
            via = "route"
        record_activity(
            session,
            ev.id,
            "assigned",
            data={"to": ev.assignee_person_id, "via": via},
            at=ev.fired_at,
        )


async def active_owner(session: AsyncSession, event: AlertEvent) -> Person | None:
    """The alert's owner if they may be mentioned (disabled people are never pinged)."""
    if event.assignee_person_id is None:
        return None
    owner = await session.get(Person, event.assignee_person_id)
    return owner if owner is not None and owner.is_active else None


# --- messages ------------------------------------------------------------------


def _headline(event: AlertEvent, domain: Domain) -> str:
    sev = {"high": "🔴", "medium": "🟠", "low": "⚪"}.get(event.severity, "•")
    return f"{sev} {kind_label(event.kind)} · {domain.fqdn}"


def assignment_message(
    event: AlertEvent,
    domain: Domain,
    person: Person | people.PersonRef,
    *,
    actor_name: str,
    channel_type: str,
) -> str:
    lines = [
        _headline(event, domain),
        f"👤 Ответственный: {people.mention(person, channel_type)} (назначил {actor_name})",
    ]
    url = alert_url(event.id)
    if url:
        lines.append(f"🔗 {url}")
    return "\n".join(lines)


def comment_message(
    event: AlertEvent,
    domain: Domain,
    body: str,
    *,
    actor_name: str,
    by_handle: dict,
    channel_type: str,
) -> str:
    if channel_type == "discord":
        body = people.neutralize_discord_mentions(body)
    elif channel_type == "slack":
        body = people.slack_escape(body)
    lines = [
        _headline(event, domain),
        f"💬 {actor_name}: {people.render_mentions(body, by_handle, channel_type)}",
    ]
    url = alert_url(event.id)
    if url:
        lines.append(f"🔗 {url}")
    return "\n".join(lines)


async def _post_to_domain_channels(
    session: AsyncSession,
    domain: Domain,
    *,
    purpose: str | None,
    render: Callable[[NotificationChannel], str],
    deliver: Deliver,
) -> list[str]:
    """Send a rendered message to the domain's channels; return names that succeeded."""
    sent: list[str] = []
    for channel in await notif.resolve_channels(session, domain, purpose=purpose):
        if await deliver(channel, render(channel)):
            sent.append(channel.name)
    return sent


# --- manual actions ------------------------------------------------------------


async def assign(
    session: AsyncSession,
    event: AlertEvent,
    domain: Domain,
    person_id: int | None,
    *,
    actor: User,
    notify: bool,
    deliver: Deliver,
) -> list[str]:
    """Set (or clear) the owner. With ``notify`` the new owner is pinged in the
    domain's instant channels. Returns the channel names the ping reached."""
    person = await session.get(Person, person_id) if person_id is not None else None
    if person_id is not None and (person is None or not person.is_active):
        raise people.PersonError("Человек не найден или отключён.")
    previous = event.assignee_person_id
    if previous == person_id:
        return []
    event.assignee_person_id = person_id
    record_activity(
        session, event.id, "assigned", actor_id=actor.id, data={"from": previous, "to": person_id}
    )
    await record_audit(
        session,
        actor_id=actor.id,
        action="assign",
        entity_type="alert",
        entity_id=event.id,
        diff={"from": previous, "to": person_id},
    )
    await session.commit()

    if not (notify and person is not None and event.state == "active"):
        return []
    ref = people.PersonRef.of(person)
    sent = await _post_to_domain_channels(
        session,
        domain,
        purpose="instant",
        render=lambda ch: assignment_message(
            event, domain, ref, actor_name=actor.login, channel_type=ch.type
        ),
        deliver=deliver,
    )
    if sent:
        record_activity(
            session,
            event.id,
            "notified",
            data={"channels": sent, "mentions": [ref.id], "reason": "assigned"},
        )
        await session.commit()
    return sent


async def ack(session: AsyncSession, event: AlertEvent, *, actor: User) -> bool:
    """«Взял в работу». The actor's linked person becomes the owner when the alert has
    none or only a group. Returns False if already acked or not active."""
    if event.state != "active" or event.acked_at is not None:
        return False
    event.acked_at = datetime.now(UTC)
    event.acked_by_id = actor.id
    data: dict[str, Any] = {}
    me = await people.person_for_user(session, actor.id)
    if me is not None and me.is_active:
        current = (
            await session.get(Person, event.assignee_person_id)
            if event.assignee_person_id is not None
            else None
        )
        if current is None or current.kind == "group":
            data = {"from": event.assignee_person_id, "to": me.id}
            event.assignee_person_id = me.id
    record_activity(session, event.id, "acked", actor_id=actor.id, data=data or None)
    await record_audit(
        session, actor_id=actor.id, action="ack", entity_type="alert", entity_id=event.id
    )
    await session.commit()
    return True


MAX_COMMENT = 2000


async def comment(
    session: AsyncSession,
    event: AlertEvent,
    domain: Domain,
    body: str,
    *,
    actor: User,
    to_channel: bool,
    deliver: Deliver,
) -> list[str]:
    """Add a comment; ``@handle`` tokens mention people. With ``to_channel`` the
    comment is posted to all of the domain's channels (any mode — the user asked for
    it) with real pings. Returns the channel names it reached."""
    body = (body or "").strip()
    if not body:
        raise people.PersonError("Комментарий пустой.")
    body = body[:MAX_COMMENT]
    by_handle = await people.people_by_handle(session)
    refs = {h: people.PersonRef.of(p) for h, p in by_handle.items()}
    mentioned = people.extract_mentions(body, refs)
    entry = record_activity(
        session,
        event.id,
        "comment",
        actor_id=actor.id,
        body=body,
        data={"mentions": [p.id for p in mentioned], "channels": []},
    )
    await record_audit(
        session,
        actor_id=actor.id,
        action="comment",
        entity_type="alert",
        entity_id=event.id,
        diff={"mentions": [p.handle for p in mentioned], "to_channel": to_channel},
    )
    await session.commit()  # the comment is saved even if a channel is down

    if not to_channel:
        return []
    sent = await _post_to_domain_channels(
        session,
        domain,
        purpose=None,
        render=lambda ch: comment_message(
            event, domain, body, actor_name=actor.login, by_handle=refs, channel_type=ch.type
        ),
        deliver=deliver,
    )
    entry.data_json = {**(entry.data_json or {}), "channels": sent}
    await session.commit()
    return sent


# --- timeline ------------------------------------------------------------------


@dataclass
class TimelineItem:
    at: datetime
    glyph: str
    color: str  # CSS var name
    text: str
    actor: str | None = None
    body: str | None = None  # comment text (rendered with @handles highlighted)
    mentions: list[str] = field(default_factory=list)

    @property
    def at_label(self) -> str:
        return _kyiv(self.at)


def _who(users: dict[int, str], user_id: int | None, default: str = "система") -> str:
    return users.get(user_id, "удалённый пользователь") if user_id is not None else default


def _person_name(persons: dict[int, Person], pid: int | None) -> str:
    if pid is None:
        return "—"
    p = persons.get(pid)
    return f"{p.name} (@{p.handle})" if p is not None else "удалённый человек"


async def timeline(session: AsyncSession, event: AlertEvent) -> list[TimelineItem]:
    acts = list(
        (
            await session.execute(
                select(AlertActivity)
                .where(AlertActivity.alert_event_id == event.id)
                .order_by(AlertActivity.at, AlertActivity.id)
            )
        )
        .scalars()
        .all()
    )
    user_ids = {a.actor_user_id for a in acts if a.actor_user_id is not None}
    person_ids: set[int] = set()
    for a in acts:
        d = a.data_json or {}
        for key in ("from", "to"):
            if isinstance(d.get(key), int):
                person_ids.add(d[key])
        person_ids.update(i for i in d.get("mentions") or [] if isinstance(i, int))
    users = (
        dict(
            (await session.execute(select(User.id, User.login).where(User.id.in_(user_ids)))).all()
        )
        if user_ids
        else {}
    )
    persons = (
        {
            p.id: p
            for p in (
                await session.execute(select(Person).where(Person.id.in_(person_ids)))
            ).scalars()
        }
        if person_ids
        else {}
    )

    p = event.payload_json or {}
    fired_text = f"Сработал: {kind_label(event.kind)}"
    if isinstance(p.get("threshold"), int):
        fired_text += f" · порог ≤{p['threshold']} дн."
    items = [TimelineItem(event.fired_at, "●", "--mag", fired_text)]

    has_delivery = False
    has_resolved = False
    for a in acts:
        d = a.data_json or {}
        who = _who(users, a.actor_user_id)
        if a.kind == "notified":
            chans = ", ".join(f"«{c}»" for c in d.get("channels") or []) or "—"
            mentioned = [_person_name(persons, i) for i in d.get("mentions") or []]
            if d.get("reason") == "assigned":
                # Sent synchronously — these channel names are confirmed deliveries.
                text = f"Пинг ответственному → {chans}"
                items.append(TimelineItem(a.at, "✔", "--ok", text, mentions=mentioned))
            else:
                # Instant alerts are queued to the worker; the real result per channel
                # is in «Доставка» (NotificationLog), so don't claim success here.
                has_delivery = True
                text = f"Поставлено в отправку: {chans} (результат — в «Доставке»)"
                items.append(TimelineItem(a.at, "→", "--cyan", text, mentions=mentioned))
        elif a.kind == "digest":
            has_delivery = True
            items.append(
                TimelineItem(a.at, "✔", "--ok", f"В сводке канала «{d.get('channel', '—')}»")
            )
        elif a.kind == "assigned":
            to, via = d.get("to"), d.get("via")
            if via == "route":
                text = f"Правило маршрутизации → {_person_name(persons, to)}"
                items.append(TimelineItem(a.at, "◆", "--cyan", text))
            elif via == "carry":
                text = f"Ответственный перенесён с прошлого порога: {_person_name(persons, to)}"
                items.append(TimelineItem(a.at, "◆", "--cyan", text))
            elif to is None:
                items.append(TimelineItem(a.at, "◇", "--dim", "Ответственный снят", actor=who))
            else:
                items.append(
                    TimelineItem(
                        a.at, "◆", "--cyan", f"Назначен: {_person_name(persons, to)}", actor=who
                    )
                )
        elif a.kind == "acked":
            text = "Взял(а) в работу"
            if d.get("to"):
                text += f" · ответственный → {_person_name(persons, d['to'])}"
            items.append(TimelineItem(a.at, "◆", "--amber", text, actor=who))
        elif a.kind == "comment":
            mentioned = [_person_name(persons, i) for i in d.get("mentions") or []]
            chans = d.get("channels") or []
            text = "Комментарий" + (f" → {', '.join(f'«{c}»' for c in chans)}" if chans else "")
            items.append(
                TimelineItem(a.at, "»", "--tx", text, actor=who, body=a.body, mentions=mentioned)
            )
        elif a.kind == "resolved":
            has_resolved = True
            reason = d.get("reason")
            if a.actor_user_id is not None or reason is None:
                items.append(TimelineItem(a.at, "✔", "--ok", "Закрыт", actor=who, body=a.body))
            elif reason == "superseded":
                text = "Заменён более срочным порогом"
                if isinstance(d.get("by"), int):
                    text += f" → алерт #{d['by']}"
                items.append(TimelineItem(a.at, "↗", "--cyan", text))
            elif reason == "archived":
                items.append(TimelineItem(a.at, "✔", "--dim", "Закрыт: домен заархивирован"))
            else:
                text = "Закрыт автоматически: проблема больше не видна"
                items.append(TimelineItem(a.at, "✔", "--ok", text))

    # Deliveries before T97 were not journaled — show the recorded moment.
    if event.notified_at is not None and not has_delivery:
        items.append(TimelineItem(event.notified_at, "→", "--cyan", "Отправлено в каналы"))
    if event.state == "resolved" and not has_resolved and event.resolved_at is not None:
        # Closed before T97 journaled closures: an event of the same kind fired at the
        # very moment of closing means it was superseded by a tighter threshold.
        successor = await session.scalar(
            select(AlertEvent.id).where(
                AlertEvent.domain_id == event.domain_id,
                AlertEvent.kind == event.kind,
                AlertEvent.id != event.id,
                AlertEvent.fired_at == event.resolved_at,
            )
        )
        if successor is not None:
            text = f"Заменён более срочным порогом → алерт #{successor}"
            items.append(TimelineItem(event.resolved_at, "↗", "--cyan", text))
        else:
            items.append(TimelineItem(event.resolved_at, "✔", "--ok", "Закрыт"))
    items.sort(key=lambda i: i.at)
    return items


# --- detailed card -------------------------------------------------------------


@dataclass
class Fact:
    label: str
    value: str
    tone: str = ""  # "", "ok", "warn", "bad"
    href: str | None = None


@dataclass
class CardDetails:
    facts: list[Fact]
    hint: str
    tables: list[dict] = field(default_factory=list)  # {title, columns, rows}


def _yes_no(value: bool | None) -> tuple[str, str]:
    return {True: ("✔ включено", "ok"), False: ("✖ выключено", "bad")}.get(
        value, ("❔ неизвестно", "warn")
    )


async def _expiry_details(session, event, domain, registrar, account) -> CardDetails:
    p = event.payload_json or {}
    facts: list[Fact] = []
    days: int | None = None
    if domain.expiry_date is not None:
        days = (domain.expiry_date - datetime.now(UTC)).days
        tone = "bad" if days <= 7 else "warn" if days <= 30 else ""
        phrase = f"просрочен {-days} дн. назад" if days < 0 else f"через {days} дн."
        facts.append(Fact("Истекает", f"{phrase} — {_kyiv(domain.expiry_date, '%d.%m.%Y')}", tone))
    if isinstance(p.get("threshold"), int):
        facts.append(Fact("Порог", f"≤{p['threshold']} дн."))
    ar_text, ar_tone = _yes_no(domain.auto_renew)
    facts.append(Fact("Автопродление", ar_text, ar_tone))
    facts.append(Fact("Регистратор", registrar or "—"))
    if account:
        facts.append(Fact("Аккаунт", account))
    if domain.renewal_price is not None:
        months = domain.renewal_period_months or 12
        facts.append(
            Fact("Продление", f"{domain.renewal_price} {domain.renewal_currency} / {months} мес.")
        )
    where = f"у регистратора {registrar}" if registrar else "у регистратора"
    if account:
        where += f" (аккаунт {account})"
    if days is not None and days < 0:
        hint = (
            f"Домен уже истёк. Если он нужен — срочно продлите {where}: идёт период "
            "восстановления. Если не нужен — заархивируйте его, чтобы алерты прекратились."
        )
    elif domain.auto_renew:
        hint = (
            f"Автопродление включено — убедитесь, что {where} есть деньги на балансе/карте; "
            "после списания дата обновится сама."
        )
    else:
        hint = f"Продлите вручную {where}. После продления дата подтянется при следующей проверке."
    return CardDetails(facts, hint)


async def _ssl_details(session, event, domain) -> CardDetails:
    p = event.payload_json or {}
    latest = await session.scalar(
        select(SslCertificate.checked_at)
        .where(SslCertificate.domain_id == domain.id)
        .order_by(SslCertificate.checked_at.desc())
        .limit(1)
    )
    rows: list[list[str]] = []
    if latest is not None:
        certs = (
            await session.execute(
                select(SslCertificate).where(
                    SslCertificate.domain_id == domain.id, SslCertificate.checked_at == latest
                )
            )
        ).scalars()
        now = datetime.now(UTC)
        for c in certs:
            left = f"{(c.valid_to - now).days} дн." if c.valid_to else "—"
            rows.append(
                [c.host, (c.issuer or "—")[:60], _kyiv(c.valid_to, "%d.%m.%Y"), left, c.error or ""]
            )
    facts = []
    if isinstance(p.get("days"), int):
        facts.append(Fact("Осталось", f"{p['days']} дн.", "bad" if p["days"] <= 7 else "warn"))
    if isinstance(p.get("threshold"), int):
        facts.append(Fact("Порог", f"≤{p['threshold']} дн."))
    hint = (
        "Перевыпустите сертификат или проверьте автообновление (certbot / Cloudflare / "
        "хостинг). Если на хосте нет сайта — это может быть ложная тревога."
    )
    tables = [
        {
            "title": "Сертификаты (последняя проверка)",
            "columns": ["Хост", "Издатель", "Действует до", "Осталось", "Ошибка"],
            "rows": rows,
        }
    ]
    return CardDetails(facts, hint, tables if rows else [])


async def _vt_details(session, event, domain) -> CardDetails:
    vt = await session.scalar(
        select(VtResult)
        .where(VtResult.domain_id == domain.id)
        .order_by(VtResult.checked_at.desc())
        .limit(1)
    )
    facts: list[Fact] = []
    if vt is not None:
        total = vt.malicious + vt.suspicious + vt.harmless + vt.undetected
        facts.append(Fact("Вредоносный", f"{vt.malicious} из {total} движков", "bad"))
        facts.append(Fact("Подозрительный", str(vt.suspicious), "warn" if vt.suspicious else ""))
        facts.append(Fact("Репутация", str(vt.reputation), "bad" if vt.reputation < 0 else ""))
        facts.append(Fact("Проверено", _kyiv(vt.checked_at)))
    else:
        facts.append(Fact("Детектов", str((event.payload_json or {}).get("malicious", "—")), "bad"))
    facts.append(
        Fact(
            "Отчёт",
            "открыть на VirusTotal",
            href=f"https://www.virustotal.com/gui/domain/{domain.punycode or domain.fqdn}",
        )
    )
    hint = (
        "Проверьте сайт на заражение и подозрительные редиректы, уберите причину и "
        "запросите перепроверку на VirusTotal. Алерт закроется сам, когда детекты уйдут."
    )
    return CardDetails(facts, hint)


async def _health_details(session, event, domain) -> CardDetails:
    hc_id = (event.payload_json or {}).get("healthcheck_id")
    hc = await session.get(HealthCheck, hc_id) if isinstance(hc_id, int) else None
    facts: list[Fact] = []
    rows: list[list[str]] = []
    if hc is not None:
        facts += [
            Fact("URL", f"{hc.method} {hc.url}", href=hc.url),
            Fact("Ожидается", hc.expected_statuses),
            Fact("Подряд неудач", str(hc.consecutive_failures), "bad"),
        ]
        results = (
            await session.execute(
                select(HealthCheckResult)
                .where(HealthCheckResult.healthcheck_id == hc.id)
                .order_by(HealthCheckResult.checked_at.desc())
                .limit(8)
            )
        ).scalars()
        for r in results:
            rows.append(
                [
                    _kyiv(r.checked_at),
                    "✔" if r.ok else "✖",
                    str(r.status_code or "—"),
                    f"{r.latency_ms} мс" if r.latency_ms is not None else "—",
                    (r.error or "")[:120],
                ]
            )
    else:
        facts.append(Fact("Health-check", f"#{hc_id} (удалён)" if hc_id else "—"))
    hint = (
        "Откройте URL проверки и посмотрите, что отвечает сервер (хостинг, редирект, WAF/"
        "Cloudflare). Алерт закроется сам, когда проверка снова пройдёт."
    )
    tables = [
        {
            "title": "Последние результаты",
            "columns": ["Когда", "OK", "Код", "Время", "Ошибка"],
            "rows": rows,
        }
    ]
    return CardDetails(facts, hint, tables if rows else [])


def _ns_details(event) -> CardDetails:
    p = event.payload_json or {}
    old = [str(x) for x in p.get("old_ns") or []]
    new = [str(x) for x in p.get("new_ns") or []]
    added = [x for x in new if x not in old]
    removed = [x for x in old if x not in new]
    facts = [
        Fact("Было", ", ".join(old) or "—"),
        Fact("Стало", ", ".join(new) or "—", "warn"),
    ]
    if added:
        facts.append(Fact("Добавлены", ", ".join(added), "warn"))
    if removed:
        facts.append(Fact("Убраны", ", ".join(removed), "bad"))
    hint = (
        "Если NS меняли вы (переезд на Cloudflare/хостинг) — закройте алерт с причиной. "
        "Если нет — срочно проверьте доступ к аккаунту регистратора: это признак угона."
    )
    return CardDetails(facts, hint)


def _check_error(c: CheckResult) -> str:
    d = c.data_json or {}
    if isinstance(d, dict):
        if d.get("error"):
            return str(d["error"])[:160]
        errs = [h.get("error") for h in d.get("hosts") or [] if isinstance(h, dict)]
        errs = [e for e in errs if e]
        if errs:
            return str(errs[0])[:160]
    return ""


@dataclass
class DomainContext:
    project: Project | None
    company: Company | None
    registrar: str | None
    account: str | None


async def domain_context(session: AsyncSession, domain: Domain) -> DomainContext:
    """Company/project and registrar/account of a domain (shared by card and message)."""
    project = await session.get(Project, domain.project_id)
    company = await session.get(Company, project.company_id) if project is not None else None
    registrar = (
        await session.scalar(select(Registrar.name).where(Registrar.id == domain.registrar_id))
        if domain.registrar_id is not None
        else None
    )
    account = (
        await session.scalar(
            select(RegistrarAccount.label).where(RegistrarAccount.id == domain.registrar_account_id)
        )
        if domain.registrar_account_id is not None
        else None
    )
    if registrar is None and domain.registrar_account_id is not None:
        registrar = await session.scalar(
            select(Registrar.name)
            .join(RegistrarAccount, RegistrarAccount.registrar_id == Registrar.id)
            .where(RegistrarAccount.id == domain.registrar_account_id)
        )
    return DomainContext(project, company, registrar, account)


async def alert_details(
    session: AsyncSession, event: AlertEvent, domain: Domain, ctx: DomainContext
) -> CardDetails:
    """«Что случилось» facts + «что делать» hint for an alert, per kind."""
    if event.kind == "expiry":
        return await _expiry_details(session, event, domain, ctx.registrar, ctx.account)
    if event.kind == "ssl":
        return await _ssl_details(session, event, domain)
    if event.kind == "vt_malicious":
        return await _vt_details(session, event, domain)
    if event.kind == "health_down":
        return await _health_details(session, event, domain)
    if event.kind == "ns_change":
        return _ns_details(event)
    return CardDetails([], "")


async def build_card(session: AsyncSession, event: AlertEvent, domain: Domain) -> dict[str, Any]:
    """Everything the alert card renders, in one place (keeps the route thin)."""
    ctx = await domain_context(session, domain)
    project, company, registrar, account = ctx.project, ctx.company, ctx.registrar, ctx.account
    details = await alert_details(session, event, domain, ctx)

    assignee = (
        await session.get(Person, event.assignee_person_id)
        if event.assignee_person_id is not None
        else None
    )
    acked_by = await session.get(User, event.acked_by_id) if event.acked_by_id else None
    resolved_by = await session.get(User, event.resolved_by_id) if event.resolved_by_id else None
    suggested = await people.route_for(session, domain, event.kind)

    other_alerts = list(
        (
            await session.execute(
                select(AlertEvent)
                .where(
                    AlertEvent.domain_id == domain.id,
                    AlertEvent.state == "active",
                    AlertEvent.id != event.id,
                )
                .order_by(AlertEvent.fired_at.desc())
            )
        )
        .scalars()
        .all()
    )
    deliveries = (
        await session.execute(
            select(NotificationLog, NotificationChannel.name, NotificationChannel.type)
            .join(NotificationChannel, NotificationChannel.id == NotificationLog.channel_id)
            .where(NotificationLog.alert_event_id == event.id)
            .order_by(NotificationLog.sent_at.desc())
            .limit(20)
        )
    ).all()
    checks = list(
        (
            await session.execute(
                select(CheckResult)
                .where(CheckResult.domain_id == domain.id)
                .order_by(CheckResult.checked_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    channels = await notif.resolve_channels(session, domain)
    all_people = await people.list_people(session, active_only=True)

    now = datetime.now(UTC)
    end = event.resolved_at or now
    return {
        "event": event,
        "domain": domain,
        "project": project,
        "company": company,
        "registrar": registrar,
        "account": account,
        "kind_label": kind_label(event.kind),
        "severity_label": severity_label(event.severity),
        "details": details,
        "assignee": assignee,
        "suggested": suggested,
        "acked_by": acked_by,
        "resolved_by": resolved_by,
        "other_alerts": other_alerts,
        "deliveries": [
            {"log": log, "channel": name, "type": ctype} for log, name, ctype in deliveries
        ],
        "checks": [{"c": c, "error": _check_error(c)} for c in checks],
        "channels": channels,
        "timeline": await timeline(session, event),
        "people": all_people,
        "open_for": _duration(end - event.fired_at),
        "fired_label": _kyiv(event.fired_at),
        "resolved_label": _kyiv(event.resolved_at) if event.resolved_at else None,
        "acked_label": _kyiv(event.acked_at) if event.acked_at else None,
    }


def _duration(delta) -> str:
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {rem // 60} мин"
    return f"{rem // 60} мин"
