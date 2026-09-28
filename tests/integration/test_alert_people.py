"""T97: people directory, routing "kind → person", auto-assignment, pings, workflow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.alert import AlertActivity, AlertEvent
from app.models.audit import AuditLog
from app.models.domain import Domain
from app.models.user import Role, User
from app.services import alert_workflow as wf
from app.services import alerts, people
from app.services import notifications as notif

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
VASYA_ID = "123456789012345678"
OPS_ROLE = "987654321098765432"


def _run(coro):
    return asyncio.run(coro)


def _person(handle: str, *, kind="person", discord=None, tg=None, user_id=None) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            p = await people.create_person(
                s,
                kind=kind,
                name=handle.title(),
                handle=handle,
                discord_id=discord,
                telegram_username=tg,
                user_id=user_id,
                actor_id=None,
            )
            return p.id

    return _run(_c())


def _route(person_id: int, kind: str = "*", company_id=None, project_id=None) -> None:
    async def _c() -> None:
        async with SessionLocal() as s:
            await people.set_route(
                s,
                company_id=company_id,
                project_id=project_id,
                kind=kind,
                person_id=person_id,
                actor_id=None,
            )

    _run(_c())


def _channel(type_: str = "discord", **kw) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            cfg = {"chat_id": "-100"} if type_ == "telegram" else {"webhook_url": "https://x/y"}
            ch = await notif.create_channel_typed(
                s,
                type=type_,
                name=kw.get("name", type_),
                config=cfg,
                company_id=kw.get("company_id"),
                project_id=None,
                is_default=kw.get("is_default", kw.get("company_id") is None),
                mode=kw.get("mode", "both"),
                digest_time=None,
                actor_id=None,
            )
            return ch.id

    return _run(_c())


def _event(event_id: int) -> AlertEvent:
    async def _c() -> AlertEvent:
        async with SessionLocal() as s:
            return await s.get(AlertEvent, event_id)

    return _run(_c())


def _activities(event_id: int) -> list[AlertActivity]:
    async def _c():
        async with SessionLocal() as s:
            rows = await s.execute(
                select(AlertActivity)
                .where(AlertActivity.alert_event_id == event_id)
                .order_by(AlertActivity.id)
            )
            return list(rows.scalars().all())

    return _run(_c())


def _fire(domain_id: int, *, sent: list) -> list[int]:
    """Run the production evaluate path (auto-assign + instant dispatch) for rdap."""

    async def _c() -> list[int]:
        async with SessionLocal() as s:
            events = await alerts.evaluate_after_check(
                s,
                None,
                domain_id,
                "rdap",
                now=NOW,
                send=lambda cid, text, eid: sent.append((cid, text, eid)),
            )
            return [e.id for e in events]

    return _run(_c())


def _set_expiry(domain_id: int, days: int) -> None:
    async def _c() -> None:
        async with SessionLocal() as s:
            d = await s.get(Domain, domain_id)
            d.expiry_date = NOW + timedelta(days=days)
            await s.commit()

    _run(_c())


# --- directory ---------------------------------------------------------------------


def test_person_validation_and_uniqueness(make_user):
    uid = make_user(login="vasya")["id"]
    _person("vasya", discord=VASYA_ID, tg="vasya_ops", user_id=uid)

    async def attempt(**kw):
        async with SessionLocal() as s:
            await people.create_person(s, actor_id=None, **kw)

    with pytest.raises(people.PersonError, match="занят"):
        _run(attempt(kind="person", name="Other", handle="VASYA"))
    with pytest.raises(people.PersonError, match="привязан"):
        _run(attempt(kind="person", name="Other", handle="other", user_id=uid))
    with pytest.raises(people.PersonError, match="Discord ID"):
        _run(attempt(kind="person", name="Bad", handle="bad", discord_id="nope"))
    # A group never keeps a telegram username or a linked account.
    gid = _person("ops", kind="group", discord=OPS_ROLE, tg="ignored_name")

    async def load(pid):
        async with SessionLocal() as s:
            return await people.get_person(s, pid)

    g = _run(load(gid))
    assert g.telegram_username is None and g.user_id is None


def test_route_resolution_tiers(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    web = make_project(acme, code="web")
    shop = make_project(acme, code="shop")
    other = make_company(code="other")
    oweb = make_project(other, code="web")
    d_web = make_domain(web, fqdn="web.com")
    d_shop = make_domain(shop, fqdn="shop.com")
    d_other = make_domain(oweb, fqdn="other.com")

    fallback = _person("fallback")
    finance = _person("finance")
    ops = _person("ops", kind="group", discord=OPS_ROLE)
    webowner = _person("webowner")
    _route(fallback, "*")  # global default
    _route(finance, "expiry", company_id=acme)
    _route(ops, "*", company_id=acme)
    _route(webowner, "expiry", project_id=web)

    async def owner(domain_id, kind):
        async with SessionLocal() as s:
            p = await people.route_for(s, await s.get(Domain, domain_id), kind)
            return p.handle if p else None

    assert _run(owner(d_web, "expiry")) == "webowner"  # project beats company
    assert _run(owner(d_web, "ssl")) == "ops"  # project tier has no ssl → company "*"
    assert _run(owner(d_shop, "expiry")) == "finance"  # company exact beats company "*"
    assert _run(owner(d_other, "vt_malicious")) == "fallback"  # global "*"

    # An inactive person is skipped → falls through to the next tier.
    async def deactivate(pid):
        async with SessionLocal() as s:
            p = await people.get_person(s, pid)
            await people.update_person(
                s,
                p,
                kind=p.kind,
                name=p.name,
                handle=p.handle,
                discord_id=p.discord_id,
                telegram_username=p.telegram_username,
                user_id=None,
                note=None,
                is_active=False,
                actor_id=None,
            )

    _run(deactivate(webowner))
    assert _run(owner(d_web, "expiry")) == "finance"


def test_set_route_replaces_same_scope_and_kind():
    a = _person("alice")
    b = _person("bob")
    _route(a, "ssl")
    _route(b, "ssl")  # same (global, ssl) → replaced, not duplicated

    async def rows():
        async with SessionLocal() as s:
            return await people.list_routes(s)

    got = _run(rows())
    assert len(got) == 1 and got[0].person.handle == "bob"


# --- auto-assignment + pings ------------------------------------------------------------


def test_fired_alert_is_assigned_by_route_and_pinged(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="soon.com", expiry_date=NOW + timedelta(days=3))
    vasya = _person("vasya", discord=VASYA_ID, tg="vasya_ops")
    _route(vasya, "expiry", company_id=acme)
    discord = _channel("discord", name="Adera alert")
    telegram = _channel("telegram", name="TG", is_default=True)

    sent: list = []
    [eid] = _fire(dom, sent=sent)

    ev = _event(eid)
    assert ev.assignee_person_id == vasya
    by_channel = {cid: text for cid, text, _ in sent}
    assert f"👤 <@{VASYA_ID}>" in by_channel[discord]  # real Discord ping
    assert "👤 @vasya_ops" in by_channel[telegram]  # Telegram mention
    assert f"/alerts/{eid}" in by_channel[discord]  # link to the card

    kinds = [(a.kind, (a.data_json or {}).get("via")) for a in _activities(eid)]
    assert ("assigned", "route") in kinds
    notified = [a for a in _activities(eid) if a.kind == "notified"][0]
    assert notified.data_json["mentions"] == [vasya]
    assert set(notified.data_json["channels"]) == {"Adera alert", "TG"}


def test_no_route_means_no_owner_and_no_ping(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="soon.com", expiry_date=NOW + timedelta(days=3))
    _channel("discord")
    sent: list = []
    [eid] = _fire(dom, sent=sent)
    assert _event(eid).assignee_person_id is None
    assert "👤" not in sent[0][1]


def test_escalation_keeps_the_manual_owner(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="esc.com", expiry_date=NOW + timedelta(days=20))
    router_pick = _person("router")
    manual = _person("manual")
    _route(router_pick, "expiry")
    admin = make_user(login="root", role=Role.admin)
    [first] = _fire(dom, sent=[])  # 30-day band → routed to @router
    assert _event(first).assignee_person_id == router_pick

    async def reassign():
        async with SessionLocal() as s:
            ev = await s.get(AlertEvent, first)
            await wf.assign(
                s,
                ev,
                await s.get(Domain, dom),
                manual,
                actor=await s.get(User, admin["id"]),
                notify=False,
                deliver=None,
            )

    _run(reassign())
    _set_expiry(dom, 5)  # crosses into the 7-day band → new event
    [second] = _fire(dom, sent=[])
    assert second != first
    assert _event(second).assignee_person_id == manual  # carried, not re-routed
    assert any((a.data_json or {}).get("via") == "carry" for a in _activities(second))


# --- manual actions ------------------------------------------------------------------


def _fake_deliver(out: list):
    async def deliver(channel, text):
        out.append((channel.name, text))
        return True

    return deliver


def _with(event_id, domain_id, user_id, fn):
    async def _c():
        async with SessionLocal() as s:
            ev = await s.get(AlertEvent, event_id)
            dom = await s.get(Domain, domain_id)
            actor = await s.get(User, user_id)
            return await fn(s, ev, dom, actor)

    return _run(_c())


def _setup_alert(make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="w.com", expiry_date=NOW + timedelta(days=20))
    [eid] = _fire(dom, sent=[])
    mgr = make_user(login="mgr", role=Role.manager)
    return eid, dom, mgr["id"]


def test_assign_pings_new_owner(make_user, make_company, make_project, make_domain):
    eid, dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)
    vasya = _person("vasya", discord=VASYA_ID)
    _channel("discord", name="Adera alert")
    out: list = []

    sent = _with(
        eid,
        dom,
        uid,
        lambda s, ev, d, u: wf.assign(
            s, ev, d, vasya, actor=u, notify=True, deliver=_fake_deliver(out)
        ),
    )
    assert sent == ["Adera alert"]
    assert f"Ответственный: <@{VASYA_ID}>" in out[0][1]
    assert "назначил mgr" in out[0][1]
    assert _event(eid).assignee_person_id == vasya
    acts = _activities(eid)
    assert any(a.kind == "assigned" and a.actor_user_id == uid for a in acts)
    assert any((a.data_json or {}).get("reason") == "assigned" for a in acts)

    # Unassign without notify: no message.
    out.clear()
    _with(
        eid,
        dom,
        uid,
        lambda s, ev, d, u: wf.assign(
            s, ev, d, None, actor=u, notify=True, deliver=_fake_deliver(out)
        ),
    )
    assert out == [] and _event(eid).assignee_person_id is None


def test_assign_rejects_inactive_or_missing_person(
    make_user, make_company, make_project, make_domain
):
    eid, dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)
    with pytest.raises(people.PersonError):
        _with(
            eid,
            dom,
            uid,
            lambda s, ev, d, u: wf.assign(s, ev, d, 9999, actor=u, notify=False, deliver=None),
        )


def test_ack_makes_linked_person_the_owner(make_user, make_company, make_project, make_domain):
    eid, dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)
    me = _person("mgr", user_id=uid)
    ok = _with(eid, dom, uid, lambda s, ev, d, u: wf.ack(s, ev, actor=u))
    assert ok is True
    ev = _event(eid)
    assert ev.acked_at is not None and ev.acked_by_id == uid
    assert ev.assignee_person_id == me
    # Second ack is a no-op.
    assert _with(eid, dom, uid, lambda s, ev, d, u: wf.ack(s, ev, actor=u)) is False


def test_ack_keeps_an_individual_owner(make_user, make_company, make_project, make_domain):
    eid, dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)
    owner = _person("owner")
    _person("mgr", user_id=uid)
    _with(
        eid,
        dom,
        uid,
        lambda s, ev, d, u: wf.assign(s, ev, d, owner, actor=u, notify=False, deliver=None),
    )
    _with(eid, dom, uid, lambda s, ev, d, u: wf.ack(s, ev, actor=u))
    assert _event(eid).assignee_person_id == owner


def test_comment_with_mentions_goes_to_channel(make_user, make_company, make_project, make_domain):
    eid, dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)
    vasya = _person("vasya", discord=VASYA_ID)
    ops = _person("ops", kind="group", discord=OPS_ROLE)
    _channel("discord", name="Adera alert", mode="digest")  # comments go to any mode
    out: list = []

    sent = _with(
        eid,
        dom,
        uid,
        lambda s, ev, d, u: wf.comment(
            s,
            ev,
            d,
            "  @vasya оплати, @ops проверьте NS @everyone  ",
            actor=u,
            to_channel=True,
            deliver=_fake_deliver(out),
        ),
    )
    assert sent == ["Adera alert"]
    text = out[0][1]
    assert f"<@{VASYA_ID}> оплати" in text and f"<@&{OPS_ROLE}> проверьте" in text
    assert "@everyone" in text  # left as text; Discord's allowed_mentions blocks the ping
    comment = [a for a in _activities(eid) if a.kind == "comment"][0]
    assert comment.body == "@vasya оплати, @ops проверьте NS @everyone"
    assert set(comment.data_json["mentions"]) == {vasya, ops}
    assert comment.data_json["channels"] == ["Adera alert"]


def test_comment_empty_is_rejected_and_local_comment_not_sent(
    make_user, make_company, make_project, make_domain
):
    eid, dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)
    _channel("discord")
    with pytest.raises(people.PersonError):
        _with(
            eid,
            dom,
            uid,
            lambda s, ev, d, u: wf.comment(
                s, ev, d, "   ", actor=u, to_channel=True, deliver=_fake_deliver([])
            ),
        )
    out: list = []
    _with(
        eid,
        dom,
        uid,
        lambda s, ev, d, u: wf.comment(
            s, ev, d, "только для своих", actor=u, to_channel=False, deliver=_fake_deliver(out)
        ),
    )
    assert out == []


def test_resolve_records_who_and_why(make_user, make_company, make_project, make_domain):
    eid, _dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)

    async def resolve():
        async with SessionLocal() as s:
            return await alerts.resolve_event(s, eid, actor_id=uid, note="  продлили до 2027  ")

    assert _run(resolve()) is True
    ev = _event(eid)
    assert ev.state == "resolved" and ev.resolved_by_id == uid
    assert ev.resolution_note == "продлили до 2027"
    assert any(a.kind == "resolved" and a.body == "продлили до 2027" for a in _activities(eid))

    async def audits():
        async with SessionLocal() as s:
            return await s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "resolve", AuditLog.entity_id == str(eid))
            )

    assert _run(audits()) == 1


def test_timeline_tells_the_story(make_user, make_company, make_project, make_domain):
    eid, dom, uid = _setup_alert(make_user, make_company, make_project, make_domain)
    vasya = _person("vasya", discord=VASYA_ID)
    _with(
        eid,
        dom,
        uid,
        lambda s, ev, d, u: wf.assign(s, ev, d, vasya, actor=u, notify=False, deliver=None),
    )
    _with(eid, dom, uid, lambda s, ev, d, u: wf.ack(s, ev, actor=u))
    _with(
        eid,
        dom,
        uid,
        lambda s, ev, d, u: wf.comment(
            s, ev, d, "@vasya глянь", actor=u, to_channel=False, deliver=None
        ),
    )

    async def tl():
        async with SessionLocal() as s:
            return await wf.timeline(s, await s.get(AlertEvent, eid))

    items = _run(tl())
    texts = [i.text for i in items]
    # Order depends on the wall clock vs the fixed NOW, so assert presence only.
    assert any(t.startswith("Сработал: Истекает домен") for t in texts)
    assert any(t.startswith("Назначен: Vasya (@vasya)") for t in texts)
    assert "Взял(а) в работу" in texts
    comment = [i for i in items if i.body == "@vasya глянь"][0]
    assert comment.actor == "mgr" and comment.mentions == ["Vasya (@vasya)"]


# --- digest -------------------------------------------------------------------------


def test_digest_carries_owners_and_journals_delivery(make_company, make_project, make_domain):
    from app.models.notification import NotificationChannel
    from app.services.digest import compose_digest, owners_line

    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d1 = make_domain(proj, fqdn="one.com", expiry_date=NOW + timedelta(days=20))
    d2 = make_domain(proj, fqdn="two.com", expiry_date=NOW + timedelta(days=25))
    vasya = _person("vasya", discord=VASYA_ID)
    _route(vasya, "expiry")
    ch = _channel("discord", name="Adera alert")
    [e1] = _fire(d1, sent=[])  # medium → not instant, waits for the digest
    [e2] = _fire(d2, sent=[])

    async def build_and_mark():
        async with SessionLocal() as s:
            channel = await s.get(NotificationChannel, ch)
            digest = await compose_digest(s, channel)
            await alerts.mark_events_notified(s, digest.event_ids, channel=channel)
            return digest

    digest = _run(build_and_mark())
    assert digest.owners[0][0].handle == "vasya" and digest.owners[0][1] == 2
    assert f"<@{VASYA_ID}> ×2" in owners_line(digest, "discord")
    rows = [r for t in digest.tiers for g in t.groups for r in g.rows]
    assert all(r.assignee is not None and r.assignee.handle == "vasya" for r in rows)
    for eid in (e1, e2):
        acts = [a for a in _activities(eid) if a.kind == "digest"]
        assert acts and acts[0].data_json["channel"] == "Adera alert"


def test_deleting_a_person_unassigns_and_drops_routes(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="gone.com", expiry_date=NOW + timedelta(days=3))
    vasya = _person("vasya")
    _route(vasya, "*")
    [eid] = _fire(dom, sent=[])
    assert _event(eid).assignee_person_id == vasya

    async def delete():
        async with SessionLocal() as s:
            await people.delete_person(s, await people.get_person(s, vasya), actor_id=None)
            return await people.list_routes(s)

    assert _run(delete()) == []
    assert _event(eid).assignee_person_id is None
