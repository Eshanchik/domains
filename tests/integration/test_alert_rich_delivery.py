"""T101: instant alerts are delivered as rich cards composed from live data."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import respx
from sqlalchemy import select

from app.db import SessionLocal
from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.models.notification import NotificationLog
from app.models.registrar import Registrar, RegistrarAccount
from app.services import alert_message, people
from app.services import notifications as notif

HOOK = "https://discord.example/api/webhooks/1/abc"
VASYA_ID = "123456789012345678"


def _run(coro):
    return asyncio.run(coro)


def _setup(make_company, make_project, make_domain, *, days: int, owner: bool = False):
    adera = make_company(code="adera", name="Adera")
    proj = make_project(adera, code="ad", name="Adera")  # same name as the company

    async def registrar() -> tuple[int, int]:
        async with SessionLocal() as s:
            r = Registrar(name="Namecheap", connector_type="namecheap")
            s.add(r)
            await s.flush()
            a = RegistrarAccount(registrar_id=r.id, label="Kingbilly")
            s.add(a)
            await s.commit()
            return r.id, a.id

    rid, aid = _run(registrar())
    dom = make_domain(
        proj,
        fqdn="rca.lol",
        expiry_date=datetime.now(UTC) + timedelta(days=days, hours=1),
        auto_renew=False,
        registrar_id=rid,
        registrar_account_id=aid,
        renewal_price=Decimal("18.48"),
        renewal_currency="USD",
    )

    async def event() -> int:
        async with SessionLocal() as s:
            owner_id = None
            if owner:
                p = await people.create_person(
                    s,
                    kind="person",
                    name="Вася",
                    handle="vasya",
                    discord_id=VASYA_ID,
                    actor_id=None,
                )
                owner_id = p.id
            ev = AlertEvent(
                domain_id=dom,
                kind="expiry",
                dedupe_key=f"{dom}:expiry:1",
                severity="high",
                state="active",
                payload_json={"days": 5, "threshold": 1},  # frozen & stale on purpose
                assignee_person_id=owner_id,
                fired_at=datetime.now(UTC),
            )
            s.add(ev)
            await s.commit()
            return ev.id

    return dom, _run(event())


def _compose(eid: int, channel_type: str):
    async def _c():
        async with SessionLocal() as s:
            ev = await s.get(AlertEvent, eid)
            return await alert_message.compose(
                s, ev, await s.get(Domain, ev.domain_id), channel_type=channel_type
            )

    return _run(_c())


def test_compose_uses_live_data_and_the_card_facts(make_company, make_project, make_domain):
    _, eid = _setup(make_company, make_project, make_domain, days=-43, owner=True)
    m = _compose(eid, "discord")
    assert m.label == "Просрочен домен"
    assert m.summary.startswith("просрочен 43 дн.")  # recomputed, not the frozen "через 5"
    assert m.location == "Adera"  # company == project → shown once
    facts = dict(m.facts)
    assert facts["Автопродление"] == "✖ выключено"
    assert facts["Регистратор"] == "Namecheap" and facts["Аккаунт"] == "Kingbilly"
    assert facts["Продление"] == "18.48 USD / 12 мес."
    assert facts["Ответственный"] == "Вася (@vasya)"
    assert "Истекает" not in facts  # already in the summary
    assert m.mention == f"<@{VASYA_ID}>"
    assert "Домен уже истёк" in m.hint
    assert _compose(eid, "telegram").mention == "Вася"  # no telegram username → no ping


def _channel() -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ch = await notif.create_channel_typed(
                s,
                type="discord",
                name="Adera alert",
                config={"webhook_url": HOOK},
                company_id=None,
                project_id=None,
                is_default=True,
                mode="both",
                digest_time=None,
                actor_id=None,
            )
            return ch.id

    return _run(_c())


@respx.mock
def test_worker_delivers_instant_alert_as_embed(make_company, make_project, make_domain):
    from app.workers.checks import _send_notification

    _, eid = _setup(make_company, make_project, make_domain, days=5, owner=True)
    cid = _channel()
    route = respx.post(HOOK).respond(204)

    _run(_send_notification(cid, "plain fallback text", eid))

    body = json.loads(route.calls.last.request.content)
    [embed] = body["embeds"]
    assert embed["title"] == "Истекает домен: rca.lol"
    assert embed["color"] == 0xE5484D
    assert embed["description"].startswith("**истекает через 5 дн.")
    assert any(f["name"] == "▸ Что делать" for f in embed["fields"])
    assert body["content"].endswith(f"👤 <@{VASYA_ID}>")
    assert body["allowed_mentions"]["users"] == [VASYA_ID]

    async def logged():
        async with SessionLocal() as s:
            return (await s.execute(select(NotificationLog))).scalars().all()

    [log] = _run(logged())
    assert log.alert_event_id == eid and log.delivery_status == "sent"


@respx.mock
def test_worker_without_alert_id_sends_the_plain_text(make_company, make_project, make_domain):
    from app.workers.checks import _send_notification

    cid = _channel()
    route = respx.post(HOOK).respond(204)
    _run(_send_notification(cid, "просто текст", None))
    body = json.loads(route.calls.last.request.content)
    assert body["content"] == "просто текст" and "embeds" not in body
