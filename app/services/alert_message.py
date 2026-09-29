"""The instant alert as a rich message (T101): Discord embed card, Telegram HTML, plain.

Built from the same data as the web alert card (``alert_workflow.alert_details``), so
the message in the channel and the page it links to never disagree. Expiry days are
recomputed from the live expiry date (not the frozen payload), so a domain that expired
43 days ago says «просрочен 43 дн.», not «через -43 дн.».
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.services import alert_workflow as wf
from app.services import people

SEVERITY_STYLE = {  # color (Discord int), emoji
    "high": (0xE5484D, "🔴"),
    "medium": (0xF5A524, "🟠"),
    "low": (0x8B949E, "⚪"),
}
# Discord hard limits.
_D_TITLE, _D_DESC, _D_FIELD_NAME, _D_FIELD_VALUE, _D_FIELDS = 256, 4096, 256, 1024, 25


@dataclass
class AlertMessage:
    event_id: int
    kind: str
    severity: str
    color: int
    emoji: str
    label: str  # «Просрочен домен», «Истекает SSL-сертификат» …
    fqdn: str
    url: str | None
    summary: str  # the one line that matters: «просрочен 43 дн. — истёк 17.08.2026»
    location: str  # «Adera» or «GT1 → Kingbilly»
    facts: list[tuple[str, str]] = field(default_factory=list)
    hint: str = ""
    owner_name: str | None = None
    mention: str = ""  # the owner in the target channel's dialect (pings)
    footer: str = ""
    fired_iso: str = ""


def _days_phrase(days: int, date: str) -> str:
    if days < 0:
        return f"просрочен {-days} дн. — истёк {date}"
    if days == 0:
        return f"истекает сегодня — {date}"
    return f"истекает через {days} дн. — {date}"


def _summary(event: AlertEvent, domain: Domain, details: wf.CardDetails) -> tuple[str, str]:
    """(label, summary) for the headline."""
    p = event.payload_json or {}
    facts = {f.label: f.value for f in details.facts}
    if event.kind == "expiry":
        if domain.expiry_date is not None:
            days = (domain.expiry_date - datetime.now(UTC)).days
            date = wf._kyiv(domain.expiry_date, "%d.%m.%Y")
            label = "Просрочен домен" if days < 0 else "Истекает домен"
            return label, _days_phrase(days, date)
        return "Истекает домен", "дата истечения неизвестна"
    if event.kind == "ssl":
        days = p.get("days")
        if isinstance(days, int) and days < 0:
            return "Истёк SSL-сертификат", f"сертификат истёк {-days} дн. назад"
        return "Истекает SSL-сертификат", f"осталось {days} дн." if days is not None else ""
    if event.kind == "vt_malicious":
        return "VirusTotal: домен помечен", facts.get("Вредоносный") or (
            f"детектов: {p.get('malicious', '—')}"
        )
    if event.kind == "health_down":
        failures = facts.get("Подряд неудач")
        tail = f" — {failures} неудач подряд" if failures else ""
        return "Не отвечает health-check", f"проверка ссылки падает{tail}"
    if event.kind == "ns_change":
        parts = []
        if facts.get("Добавлены"):
            parts.append(f"добавлены {facts['Добавлены']}")
        if facts.get("Убраны"):
            parts.append(f"убраны {facts['Убраны']}")
        return "Сменились NS", "; ".join(parts) or "набор NS изменился"
    return wf.kind_label(event.kind), ""


# Facts already said in the summary (don't repeat them as fields).
_IN_SUMMARY = {"expiry": {"Истекает"}, "ssl": {"Осталось"}, "vt_malicious": {"Вредоносный"}}


async def compose(
    session: AsyncSession, event: AlertEvent, domain: Domain, *, channel_type: str
) -> AlertMessage:
    ctx = await wf.domain_context(session, domain)
    details = await wf.alert_details(session, event, domain, ctx)
    label, summary = _summary(event, domain, details)
    color, emoji = SEVERITY_STYLE.get(event.severity, SEVERITY_STYLE["low"])

    project = ctx.project.name if ctx.project else None
    company = ctx.company.name if ctx.company else None
    if project and company and project != company:
        location = f"{company} → {project}"
    else:
        location = company or project or ""

    skip = _IN_SUMMARY.get(event.kind, set())
    facts = [(f.label, f.value) for f in details.facts if f.label not in skip and f.value]
    owner = await wf.active_owner(session, event)
    if owner is not None:
        facts.append(("Ответственный", f"{owner.name} (@{owner.handle})"))

    fired = event.fired_at or datetime.now(UTC)
    return AlertMessage(
        event_id=event.id,
        kind=event.kind,
        severity=event.severity,
        color=color,
        emoji=emoji,
        label=label,
        fqdn=domain.fqdn,
        url=wf.alert_url(event.id),
        summary=summary,
        location=location,
        facts=facts,
        hint=details.hint,
        owner_name=owner.name if owner else None,
        mention=people.mention(owner, channel_type) if owner else "",
        footer=f"Алерт #{event.id} · {wf.severity_label(event.severity)}",
        fired_iso=fired.astimezone(UTC).isoformat(),
    )


# --- renderers ------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_discord(m: AlertMessage) -> dict:
    """A webhook body with one embed card. The ping lives in ``content`` (Discord never
    pings from inside an embed); the headline there also feeds push notifications."""
    fields = [
        {
            "name": _cut(label, _D_FIELD_NAME),
            "value": _cut(value, _D_FIELD_VALUE),
            "inline": len(value) <= 40,
        }
        for label, value in m.facts[: _D_FIELDS - 1]
    ]
    if m.hint:
        fields.append(
            {"name": "▸ Что делать", "value": _cut(m.hint, _D_FIELD_VALUE), "inline": False}
        )
    embed: dict = {
        "title": _cut(f"{m.label}: {m.fqdn}", _D_TITLE),
        "color": m.color,
        "description": _cut(f"**{m.summary}**", _D_DESC) if m.summary else "",
        "fields": fields,
        "footer": {"text": m.footer},
        "timestamp": m.fired_iso,
    }
    if m.url:
        embed["url"] = m.url
    if m.location:
        embed["author"] = {"name": _cut(f"DomainGuard · {m.location}", _D_TITLE)}
    content = f"{m.emoji} **{m.label}** · {m.fqdn}"
    if m.mention:
        content += f"\n👤 {m.mention}"
    return {"username": "DomainGuard", "content": _cut(content, 2000), "embeds": [embed]}


def render_telegram(m: AlertMessage) -> str:
    """Telegram HTML (parse_mode=HTML). ``@username`` mentions stay live."""

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    head = f"{m.emoji} <b>{esc(m.label)}</b> · "
    head += (
        f'<a href="{html.escape(m.url, quote=True)}">{esc(m.fqdn)}</a>' if m.url else esc(m.fqdn)
    )
    lines = [head]
    if m.summary:
        lines.append(f"<b>{esc(m.summary)}</b>")
    if m.location:
        lines.append(f"📁 {esc(m.location)}")
    lines += [f"• {esc(label)}: {esc(value)}" for label, value in m.facts]
    if m.hint:
        lines.append(f"▸ <i>Что делать:</i> {esc(m.hint)}")
    if m.mention:
        lines.append(f"👤 {esc(m.mention)}")
    lines.append(f"<i>{esc(m.footer)}</i>")
    return "\n".join(lines)


def render_plain(m: AlertMessage) -> str:
    """Slack / generic webhook / fallback."""
    lines = [f"{m.emoji} {m.label} · {m.fqdn}"]
    if m.summary:
        lines.append(m.summary)
    if m.location:
        lines.append(f"📁 {m.location}")
    lines += [f"• {label}: {value}" for label, value in m.facts]
    if m.hint:
        lines.append(f"▸ Что делать: {m.hint}")
    if m.mention:
        lines.append(f"👤 {m.mention}")
    if m.url:
        lines.append(f"🔗 {m.url}")
    return "\n".join(lines)
