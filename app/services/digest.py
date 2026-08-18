"""Daily digest: per-channel summary of active alerts (SPEC FR-AL-5).

The digest is built into a structured :class:`Digest` (scope, severity tiers, rows)
that each channel renders natively — Discord embeds, Telegram HTML, plain text
elsewhere. Days-until-expiry are **recomputed from the domain's current expiry_date**
at build time (not the frozen alert payload), so a renewed domain re-buckets instead
of showing a stale count. Presentation only: AlertEvents, dedupe and thresholds are
untouched.

Delivery: the scheduler has no egress, so it only claims the per-day slot and enqueues
``send_digest``; the worker (which has egress) re-composes and renders. The web
"send now" runs in the api (which has egress) and renders synchronously.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.alert import AlertEvent
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.notification import NotificationChannel
from app.models.registrar import RegistrarAccount

log = logging.getLogger("services.digest")

KYIV = ZoneInfo("Europe/Kyiv")

# Severity tiers → (Discord color int, emoji, title).
_TIERS: dict[str, tuple[int, str, str]] = {
    "crit": (0xE5484D, "🔴", "Критично"),
    "warn": (0xF5A524, "🟠", "Внимание"),
    "info": (0x30A46C, "🟢", "К сведению"),
}
_TIER_ORDER = ("crit", "warn", "info")


def _days_until(when: datetime, now: datetime) -> int:
    return (when - now).days


def _expiry_bucket(days: int) -> tuple[int, str, str]:
    """Fine-grained group (order, emoji, title) within a tier."""
    if days < 0:
        return (0, "💀", "Просрочены")
    if days <= 7:
        return (1, "🔴", "Истекают ≤7 дней")
    if days <= 30:
        return (2, "🟠", "Истекают 8–30 дней")
    if days <= 60:
        return (3, "🟡", "Истекают 31–60 дней")
    return (4, "⚪", "Истекают 60+ дней")


def _detects_word(n: int) -> str:
    """Russian plural for VirusTotal detections: 1 детект / 3 детекта / 5 детектов."""
    ones, tens = n % 10, n % 100
    if ones == 1 and tens != 11:
        return "детект"
    if 2 <= ones <= 4 and not 12 <= tens <= 14:
        return "детекта"
    return "детектов"


def _auto_renew_mark(auto_renew: bool | None) -> str:
    return {True: "🔄", False: "🚫"}.get(auto_renew, "❔")


def _classify(kind: str, days: int | None) -> tuple[str, int, str, str]:
    """Map an alert to (tier_key, group_order, group_emoji, group_title)."""
    if kind == "expiry":
        d = days if isinstance(days, int) else 9999
        order, emoji, title = _expiry_bucket(d)
        tier = "crit" if d <= 7 else "warn" if d <= 30 else "info"
        return tier, order, emoji, title
    if kind == "ssl":
        d = days if isinstance(days, int) else 9999
        tier = "crit" if d <= 7 else "warn" if d <= 14 else "info"
        return tier, 5, "🔒", "Истекает SSL"
    if kind == "vt_malicious":
        return "crit", 6, "🚨", "VirusTotal"
    if kind == "health_down":
        return "crit", 7, "🔴", "Health-check недоступны"
    if kind == "ns_change":
        return "crit", 8, "🛡️", "Смена NS"
    return "info", 9, "•", kind


# --- structured digest -------------------------------------------------------


@dataclass
class DigestRow:
    fqdn: str
    kind: str
    url: str | None = None
    days: int | None = None  # recomputed (expiry/ssl)
    expiry: str | None = None  # display date, Europe/Kyiv
    account: str | None = None
    project: str | None = None
    auto_renew: bool | None = None
    detail: str = ""  # ready tail for non-expiry kinds (e.g. "3 детекта")
    is_stale: bool = False
    sort: int = 0


@dataclass
class DigestGroup:
    order: int
    emoji: str
    title: str
    rows: list[DigestRow] = field(default_factory=list)


@dataclass
class DigestTier:
    key: str
    color: int
    emoji: str
    title: str
    groups: list[DigestGroup] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(len(g.rows) for g in self.groups)


@dataclass
class Digest:
    scope_name: str
    dashboard_url: str | None
    generated_label: str
    total: int
    tiers: list[DigestTier] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return next((t.count for t in self.tiers if t.key == "crit"), 0)


async def _scope_name(session: AsyncSession, channel: NotificationChannel) -> str:
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


async def compose_digest(session: AsyncSession, channel: NotificationChannel) -> Digest | None:
    """Build the structured digest for a channel, or None if there is nothing to report.

    Only **active** (non-archived) domains are included; expiry days are recomputed from
    the live ``Domain.expiry_date`` and grouped into severity tiers.
    """
    scope = await scoped_domain_ids(session, channel)
    stmt = (
        select(
            AlertEvent,
            Domain.id,
            Domain.fqdn,
            Domain.expiry_date,
            Domain.auto_renew,
            RegistrarAccount.label,
            Project.name,
        )
        .join(Domain, Domain.id == AlertEvent.domain_id)
        .join(Project, Project.id == Domain.project_id)
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

    now = datetime.now(UTC)
    base = (settings.public_base_url or "").rstrip("/")
    tier_groups: dict[str, dict[int, DigestGroup]] = {k: {} for k in _TIER_ORDER}
    total = 0

    for event, did, fqdn, exp, auto_renew, account, project in rows:
        total += 1
        p = event.payload_json or {}
        kind = event.kind
        days: int | None = None
        expiry_disp: str | None = None
        detail = ""
        is_stale = False

        if kind == "expiry" and exp is not None:
            days = _days_until(exp, now)  # recomputed — the frozen payload day is ignored
            expiry_disp = exp.astimezone(KYIV).strftime("%Y-%m-%d")
        elif kind in ("expiry", "ssl"):
            d = p.get("days")
            days = d if isinstance(d, int) else None
        if kind in ("expiry", "ssl"):
            threshold = p.get("threshold")
            if isinstance(days, int) and isinstance(threshold, int) and days > threshold:
                is_stale = True  # more days left than the band it fired in — likely renewed
        elif kind == "vt_malicious":
            m = int(p.get("malicious") or 0)
            detail = f"{m} {_detects_word(m)}"
        elif kind == "health_down":
            detail = "недоступен"
        elif kind == "ns_change":
            detail = "сменились NS"

        tier_key, order, emoji, title = _classify(kind, days)
        row = DigestRow(
            fqdn=fqdn,
            kind=kind,
            url=f"{base}/domains/{did}" if base else None,
            days=days,
            expiry=expiry_disp,
            account=account,
            project=project,
            auto_renew=auto_renew,
            detail=detail,
            is_stale=is_stale,
            sort=days if isinstance(days, int) else 10_000,
        )
        group = tier_groups[tier_key].setdefault(order, DigestGroup(order, emoji, title))
        group.rows.append(row)

    tiers: list[DigestTier] = []
    for key in _TIER_ORDER:
        groups = [tier_groups[key][o] for o in sorted(tier_groups[key])]
        for g in groups:
            g.rows.sort(key=lambda r: r.sort)
        if groups:
            color, emoji, title = _TIERS[key]
            tiers.append(DigestTier(key, color, emoji, title, groups))
    if not tiers:
        return None

    generated = datetime.now(KYIV).strftime("%d.%m.%Y %H:%M")
    return Digest(
        scope_name=await _scope_name(session, channel),
        dashboard_url=base or None,
        generated_label=f"{generated} · Kyiv · дни пересчитаны на сейчас",
        total=total,
        tiers=tiers,
    )


# --- renderers ---------------------------------------------------------------


def _days_phrase(days: int | None) -> str:
    if days is None:
        return ""
    return f"просрочен {-days} дн." if days < 0 else f"через {days} дн."


def _row_tail(row: DigestRow) -> list[str]:
    """Common (markup-free) trailing parts for a row, in order."""
    parts: list[str] = []
    if row.kind == "expiry":
        parts += [_days_phrase(row.days)]
        if row.expiry:
            parts.append(f"до {row.expiry}")
        parts.append(_auto_renew_mark(row.auto_renew))
        if row.account:
            parts.append(row.account)
    elif row.kind == "ssl":
        parts.append(_days_phrase(row.days))
        if row.account:
            parts.append(row.account)
    else:
        parts.append(row.detail)
        if row.account:
            parts.append(row.account)
    if row.is_stale:
        parts.append("⚠ вероятно продлён")
    return [p for p in parts if p]


def render_plain(d: Digest) -> str:
    """Plain-text layout (generic webhook + fallback + tests)."""
    lines = [
        f"📋 DomainGuard · {d.scope_name} — ежедневная сводка",
        f"Активных алертов: {d.total} · требуют действий: {d.action_count}",
    ]
    for t in d.tiers:
        lines.append(f"\n{t.emoji} {t.title} — {t.count}")
        for g in t.groups:
            lines.append(f"  {g.emoji} {g.title} · {len(g.rows)}")
            for r in g.rows:
                lines.append("    • " + " · ".join([r.fqdn, *_row_tail(r)]))
    return "\n".join(lines)


def render_telegram_html(d: Digest) -> str:
    """Telegram HTML (parse_mode=HTML): bold headers, linked domains."""

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    def attr(s: str) -> str:  # attribute-context escape (quotes too) for href values
        return html.escape(s, quote=True)

    def link(r: DigestRow) -> str:
        return f'<a href="{attr(r.url)}">{esc(r.fqdn)}</a>' if r.url else f"<b>{esc(r.fqdn)}</b>"

    head = [
        f"<b>📋 DomainGuard · {esc(d.scope_name)} — ежедневная сводка</b>",
        f"Активных алертов: <b>{d.total}</b> · требуют действий: <b>{d.action_count}</b>",
    ]
    if d.dashboard_url:
        head.append(f'<a href="{attr(d.dashboard_url)}">Открыть дашборд →</a>')
    head.append(f"<i>Сводка за {esc(d.generated_label)}</i>")
    lines = head
    for t in d.tiers:
        lines.append(f"\n<b>{t.emoji} {t.title} — {t.count}</b>")
        for g in t.groups:
            lines.append(f"{g.emoji} <b>{esc(g.title)} · {len(g.rows)}</b>")
            for r in g.rows:
                tail = " · ".join(esc(p) for p in _row_tail(r))
                lines.append(f"• {link(r)}{(' — ' + tail) if tail else ''}")
    return "\n".join(lines)


def _row_discord(r: DigestRow) -> str:
    name = f"[{r.fqdn}]({r.url})" if r.url else f"**{r.fqdn}**"
    tail = " · ".join(_row_tail(r))
    return f"• {name}{(' — ' + tail) if tail else ''}"


def _pack_field(lines: list[str], dashboard: str | None, limit: int = 1024) -> str:
    """Join lines into a Discord field value ≤ limit, truncating the tail if needed."""
    out: list[str] = []
    used = 0
    for i, ln in enumerate(lines):
        piece = ln if not out else "\n" + ln
        if used + len(piece) > limit:
            rest = len(lines) - i
            tail = f"\n…ещё {rest}"
            if dashboard:
                tail += f" · [дашборд →]({dashboard})"
            if used + len(tail) <= limit:
                out.append(tail)
            break
        out.append(piece)
        used += len(piece)
    return "".join(out) or "—"


# Discord hard limits: a single embed and a whole message are both capped at 6000
# chars (title + names + values + footer/author), ≤25 fields/embed, ≤10 embeds/message.
_D_EMBED_CHARS = 5500  # per-embed budget, margin under 6000
_D_MSG_CHARS = 6000
_D_FIELDS = 25
_D_EMBEDS = 10


def _embed_size(e: dict) -> int:
    n = len(e.get("title", "")) + len(e.get("description", ""))
    n += len(e.get("author", {}).get("name", "")) + len(e.get("footer", {}).get("text", ""))
    for f in e.get("fields", []):
        n += len(f.get("name", "")) + len(f.get("value", ""))
    return n


def _tier_embeds(t: DigestTier, dashboard: str | None) -> list[dict]:
    """Render a tier into one or more embeds, each ≤ _D_EMBED_CHARS and ≤25 fields."""
    title = f"{t.emoji} {t.title} — {t.count}"
    out: list[dict] = []
    cur: dict = {"title": title, "color": t.color, "fields": []}
    used = len(title)
    for g in t.groups:
        name = f"{g.emoji} {g.title} · {len(g.rows)}"
        value = _pack_field([_row_discord(r) for r in g.rows], dashboard)
        add = len(name) + len(value)
        if cur["fields"] and (used + add > _D_EMBED_CHARS or len(cur["fields"]) >= _D_FIELDS):
            out.append(cur)
            cont = f"{title} (продолжение)"
            cur = {"title": cont, "color": t.color, "fields": []}
            used = len(cont)
        cur["fields"].append({"name": name, "value": value, "inline": False})
        used += add
    out.append(cur)
    return out


def render_discord(d: Digest) -> list[dict]:
    """Discord webhook message bodies: a lead embed + per-tier cards, packed so no embed
    or message exceeds Discord's 6000-char / 10-embed caps."""
    lead = {
        "author": {
            "name": f"DomainGuard · {d.scope_name}",
            **({"url": d.dashboard_url} if d.dashboard_url else {}),
        },
        "title": "Ежедневная сводка",
        "description": f"**{d.total}** активных алертов · **{d.action_count}** требуют действий"
        + (f"\n[Открыть дашборд →]({d.dashboard_url})" if d.dashboard_url else ""),
        "color": d.tiers[0].color,
        "fields": [
            {"name": f"{t.emoji} {t.title}", "value": f"**{t.count}**", "inline": True}
            for t in d.tiers
        ],
        "footer": {"text": f"Сводка за {d.generated_label}"},
    }
    embeds: list[dict] = [lead]
    for t in d.tiers:
        embeds.extend(_tier_embeds(t, d.dashboard_url))

    # Pack embeds into messages within the 6000-char and 10-embed per-message caps.
    messages: list[dict] = []
    cur: list[dict] = []
    used = 0
    for e in embeds:
        size = _embed_size(e)
        if cur and (used + size > _D_MSG_CHARS or len(cur) >= _D_EMBEDS):
            messages.append({"username": "DomainGuard", "embeds": cur})
            cur, used = [], 0
        cur.append(e)
        used += size
    if cur:
        messages.append({"username": "DomainGuard", "embeds": cur})
    return messages


# --- scheduling --------------------------------------------------------------


def _digest_key(channel_id: int, day: str) -> str:
    return f"digest:{channel_id}:{day}"


async def run_digests(
    session: AsyncSession,
    redis: aioredis.Redis,
    *,
    now_kyiv: datetime,
    send: Callable[[int], None],
) -> list[int]:
    """Claim the per-day slot for each due channel and enqueue its digest send.

    The scheduler cannot reach the network, so it only enqueues (``send(channel_id)``);
    the worker re-composes and delivers. Idempotency: a Redis SET NX marker per
    (channel, day) so a restart or extra tick cannot double-send.
    """
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
        claimed = await redis.set(_digest_key(channel.id, day), "1", nx=True, ex=2 * 24 * 3600)
        if not claimed:
            continue
        send(channel.id)
        sent.append(channel.id)
    return sent
