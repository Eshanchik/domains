"""Daily digest: composition by channel scope + idempotency (DB + Redis)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.db import SessionLocal, get_redis
from app.models.alert import AlertEvent
from app.models.notification import NotificationChannel
from app.services import notifications as notif
from app.services.digest import (
    compose_digest,
    render_discord,
    render_plain,
    render_telegram_html,
    run_digests,
)

KYIV = ZoneInfo("Europe/Kyiv")


def _run(coro):
    return asyncio.run(coro)


def _add_event(domain_id: int, kind="expiry", days=5, key=None) -> None:
    async def _a():
        async with SessionLocal() as s:
            s.add(
                AlertEvent(
                    domain_id=domain_id,
                    kind=kind,
                    dedupe_key=key or f"{domain_id}:{kind}",
                    severity="medium",
                    state="active",
                    fired_at=datetime.now(UTC),
                    payload_json={"days": days},
                )
            )
            await s.commit()

    _run(_a())


def _make_channel(**kw) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ch = await notif.create_channel(
                s,
                name=kw.get("name", "ch"),
                chat_id="-100",
                company_id=kw.get("company_id"),
                project_id=kw.get("project_id"),
                is_default=kw.get("is_default", False),
                mode=kw.get("mode", "digest"),
                digest_time=kw.get("digest_time", "09:00"),
                actor_id=None,
            )
            return ch.id

    return _run(_c())


def test_compose_scoped_to_project(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    p2 = make_project(acme, code="shop")
    d1 = make_domain(p1, fqdn="in-scope.com")
    d2 = make_domain(p2, fqdn="out-scope.com")
    _add_event(d1)
    _add_event(d2)
    ch = _make_channel(project_id=p1)

    async def compose():
        async with SessionLocal() as s:
            channel = await s.get(NotificationChannel, ch)
            return await compose_digest(s, channel)

    text = render_plain(_run(compose()))
    assert "in-scope.com" in text
    assert "out-scope.com" not in text
    assert "ежедневная сводка" in text


def test_compose_excludes_archived_domains(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    live = make_domain(p1, fqdn="live.com")
    dead = make_domain(p1, fqdn="dead.com", is_active=False)  # archived
    _add_event(live)
    _add_event(dead, days=-1638)  # stale alert on a retired domain
    ch = _make_channel(company_id=acme)

    async def compose():
        async with SessionLocal() as s:
            return await compose_digest(s, await s.get(NotificationChannel, ch))

    text = render_plain(_run(compose()))
    assert "live.com" in text
    assert "dead.com" not in text  # archived domain never pollutes the digest


def test_compose_groups_by_urgency_and_shows_account(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")

    async def _acct() -> int:
        from app.models.registrar import Registrar, RegistrarAccount

        async with SessionLocal() as s:
            r = Registrar(name="NC", connector_type="namecheap")
            s.add(r)
            await s.flush()
            a = RegistrarAccount(registrar_id=r.id, label="Kingbilly", credentials_enc="x")
            s.add(a)
            await s.commit()
            await s.refresh(a)
            return a.id

    acct_id = _run(_acct())
    urgent = make_domain(p1, fqdn="urgent.com", registrar_account_id=acct_id)
    later = make_domain(p1, fqdn="later.com", registrar_account_id=acct_id)
    _add_event(urgent, days=3)
    _add_event(later, days=45)
    ch = _make_channel(company_id=acme, name="ACME chan")

    async def compose():
        async with SessionLocal() as s:
            return await compose_digest(s, await s.get(NotificationChannel, ch))

    text = render_plain(_run(compose()))
    assert "DomainGuard · ACME" in text  # header names the scope (company)
    assert "≤7 дней" in text and "31–60 дней" in text  # urgency buckets
    assert "Kingbilly" in text  # registrar account per line
    # urgent (3d, critical tier) is listed before later (45d, info tier)
    assert text.index("urgent.com") < text.index("later.com")


def test_archiving_resolves_active_alerts(make_company, make_project, make_domain):
    from sqlalchemy import select

    from app.models.domain import Domain
    from app.services import domains as domains_svc

    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    did = make_domain(p1, fqdn="retire.com")
    _add_event(did)

    async def archive_and_read():
        async with SessionLocal() as s:
            d = await s.get(Domain, did)
            await domains_svc.set_archived(s, d, True, actor_id=None)
        async with SessionLocal() as s:
            return (
                (await s.execute(select(AlertEvent.state).where(AlertEvent.domain_id == did)))
                .scalars()
                .all()
            )

    states = _run(archive_and_read())
    assert states and all(st == "resolved" for st in states)  # no lingering active alerts


def test_compose_empty_returns_none(make_company, make_project):
    acme = make_company(code="acme")
    make_project(acme, code="web")
    ch = _make_channel(is_default=True)

    async def compose():
        async with SessionLocal() as s:
            return await compose_digest(s, await s.get(NotificationChannel, ch))

    assert _run(compose()) is None  # no active events


def test_run_digests_idempotent_per_day(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    d1 = make_domain(p1, fqdn="example.com")
    _add_event(d1)
    ch = _make_channel(is_default=True, digest_time="09:00", mode="both")
    now = datetime(2026, 7, 20, 9, 0, tzinfo=KYIV)

    sent: list[int] = []

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                first = await run_digests(s, redis, now_kyiv=now, send=lambda cid: sent.append(cid))
            async with SessionLocal() as s:
                second = await run_digests(
                    s, redis, now_kyiv=now, send=lambda cid: sent.append(cid)
                )
            return first, second
        finally:
            await redis.aclose()

    first, second = _run(run())
    assert first == [ch]
    assert second == []  # already sent today
    assert sent == [ch]


def test_run_digests_only_at_matching_time(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    d1 = make_domain(p1, fqdn="example.com")
    _add_event(d1)
    _make_channel(is_default=True, digest_time="10:00", mode="digest")
    now = datetime(2026, 7, 20, 9, 0, tzinfo=KYIV)  # 09:00, channel wants 10:00

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                return await run_digests(s, redis, now_kyiv=now, send=lambda cid: None)
        finally:
            await redis.aclose()

    assert _run(run()) == []  # not this minute


def test_detects_word_plural():
    from app.services.digest import _detects_word

    assert _detects_word(1) == "детект"
    assert _detects_word(3) == "детекта"
    assert _detects_word(6) == "детектов"
    assert _detects_word(11) == "детектов"
    assert _detects_word(21) == "детект"


def test_expiry_days_recomputed_from_live_date(make_company, make_project, make_domain):
    """The fix: days come from the domain's live expiry_date, not the frozen payload."""
    from datetime import timedelta

    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    future = datetime.now(UTC) + timedelta(days=360)  # actually ~1 year out
    did = make_domain(p1, fqdn="renewed.com", expiry_date=future)

    async def add():
        async with SessionLocal() as s:
            s.add(
                AlertEvent(
                    domain_id=did,
                    kind="expiry",
                    dedupe_key="k",
                    severity="high",
                    state="active",
                    fired_at=datetime.now(UTC),
                    payload_json={"days": 1, "threshold": 7},  # stale "1 day" from fire time
                )
            )
            await s.commit()

    _run(add())
    ch = _make_channel(company_id=acme)

    async def compose():
        async with SessionLocal() as s:
            return await compose_digest(s, await s.get(NotificationChannel, ch))

    text = render_plain(_run(compose()))
    assert "renewed.com" in text
    assert "60+ дней" in text  # re-bucketed by recomputed days, not the stale "1 day"
    assert "≤7 дней" not in text  # no longer in the critical band
    assert "вероятно продлён" in text  # flagged as stale (more days than the fired threshold)


def test_render_discord_structure(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    d = make_domain(p1, fqdn="crit.com")
    _add_event(d, days=2)  # critical (≤7)
    ch = _make_channel(company_id=acme)

    async def compose():
        async with SessionLocal() as s:
            return await compose_digest(s, await s.get(NotificationChannel, ch))

    bodies = render_discord(_run(compose()))
    assert len(bodies) == 1
    embeds = bodies[0]["embeds"]
    assert embeds[0]["title"] == "Ежедневная сводка"  # lead embed
    assert embeds[0]["color"] == 0xE5484D  # red — a critical alert is present
    assert any("Критично" in e.get("title", "") for e in embeds[1:])
    assert "crit.com" in str(embeds)  # domain rendered in a field


def test_render_telegram_html(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    d = make_domain(p1, fqdn="tg.com")
    _add_event(d, days=2)
    ch = _make_channel(company_id=acme)

    async def compose():
        async with SessionLocal() as s:
            return await compose_digest(s, await s.get(NotificationChannel, ch))

    html_text = render_telegram_html(_run(compose()))
    assert "<b>" in html_text
    assert '<a href="' in html_text and "tg.com" in html_text


def test_render_discord_respects_size_limits():
    """A mixed-kind, high-volume tier must be split so no embed/message exceeds Discord's
    6000-char / 10-embed / 25-field / 1024-value caps."""
    from app.services.digest import (
        Digest,
        DigestGroup,
        DigestRow,
        DigestTier,
        _embed_size,
        render_discord,
    )

    groups = []
    for order, emoji, title in [
        (0, "💀", "Просрочены"),
        (1, "🔴", "Истекают ≤7 дней"),
        (5, "🔒", "Истекает SSL"),
        (6, "🚨", "VirusTotal"),
        (7, "🔴", "Health-check недоступны"),
        (8, "🛡️", "Смена NS"),
    ]:
        rows = [
            DigestRow(
                fqdn=f"domain-{order}-{i}.example.com",
                kind="expiry",
                days=1,
                url=f"https://dg.example/domains/{order}{i}",
                account="Account",
            )
            for i in range(20)
        ]
        groups.append(DigestGroup(order, emoji, title, rows))
    d = Digest(
        scope_name="Big",
        dashboard_url="https://dg.example",
        generated_label="x",
        total=120,
        tiers=[DigestTier("crit", 0xE5484D, "🔴", "Критично", groups)],
    )
    messages = render_discord(d)
    assert len(messages) >= 1
    for m in messages:
        assert len(m["embeds"]) <= 10
        assert sum(_embed_size(e) for e in m["embeds"]) <= 6000  # per-message cap
        for e in m["embeds"]:
            assert _embed_size(e) <= 6000  # per-embed cap
            assert len(e.get("fields", [])) <= 25
            for f in e.get("fields", []):
                assert len(f["value"]) <= 1024


def test_telegram_html_escapes_href():
    from app.services.digest import (
        Digest,
        DigestGroup,
        DigestRow,
        DigestTier,
        render_telegram_html,
    )

    d = Digest(
        scope_name="S",
        dashboard_url='https://x/?a=1&b=2"z',
        generated_label="g",
        total=1,
        tiers=[
            DigestTier(
                "crit",
                0,
                "🔴",
                "Критично",
                [
                    DigestGroup(
                        1,
                        "🔴",
                        "g",
                        [DigestRow(fqdn="a.com", kind="expiry", days=1, url='https://x/d/1?q="&x')],
                    )
                ],
            )
        ],
    )
    out = render_telegram_html(d)
    assert "&quot;" in out and "&amp;" in out  # href escaped for attribute context
    assert 'href="https://x/?a=1&b=2"z"' not in out  # raw unescaped href absent


def test_digest_delivers_each_alert_once(make_company, make_project, make_domain):
    """Deliver-once: after a digest is sent (events marked notified_at), the same active
    alert is not repeated in the next digest — the daily-repeat spam is gone."""
    from app.services.alerts import mark_events_notified

    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    d = make_domain(p1, fqdn="once.com")
    _add_event(d)  # active, notified_at NULL
    ch = _make_channel(company_id=acme)

    async def run():
        async with SessionLocal() as s:
            dig1 = await compose_digest(s, await s.get(NotificationChannel, ch))
            await mark_events_notified(s, dig1.event_ids)  # simulate a successful send
        async with SessionLocal() as s:
            dig2 = await compose_digest(s, await s.get(NotificationChannel, ch))
        return dig1, dig2

    dig1, dig2 = _run(run())
    assert dig1 is not None and "once.com" in render_plain(dig1)
    assert dig2 is None  # already delivered → not repeated the next day
