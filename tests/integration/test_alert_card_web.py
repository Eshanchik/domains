"""T97: /people admin page, the detailed alert card and its actions over HTTP."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models.alert import AlertActivity, AlertEvent
from app.models.person import AlertRoute, Person
from app.models.user import Role
from app.services import notifications as notif
from app.services import people

VASYA_ID = "123456789012345678"


def _run(coro):
    return asyncio.run(coro)


def _login(client, make_user, login: str, role: Role, **kw) -> dict:
    u = make_user(login=login, password="password123", role=role, **kw)
    client.post("/login", data={"login": login, "password": "password123"})
    return u


def _person(handle: str, **kw) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            p = await people.create_person(
                s,
                kind=kw.get("kind", "person"),
                name=kw.get("name", handle.title()),
                handle=handle,
                discord_id=kw.get("discord"),
                telegram_username=kw.get("tg"),
                user_id=kw.get("user_id"),
                actor_id=None,
            )
            return p.id

    return _run(_c())


def _channel(**kw) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ch = await notif.create_channel_typed(
                s,
                type="discord",
                name=kw.get("name", "Adera alert"),
                config={"webhook_url": "https://hooks.example/x"},
                company_id=None,
                project_id=None,
                is_default=True,
                mode="both",
                digest_time=None,
                actor_id=None,
            )
            return ch.id

    return _run(_c())


def _alert(domain_id: int, kind: str = "expiry", severity: str = "medium", **kw) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ev = AlertEvent(
                domain_id=domain_id,
                kind=kind,
                dedupe_key=f"{domain_id}:{kind}",
                severity=severity,
                state="active",
                payload_json=kw.get("payload", {"days": 20, "threshold": 30}),
                assignee_person_id=kw.get("assignee"),
            )
            s.add(ev)
            await s.commit()
            await s.refresh(ev)
            return ev.id

    return _run(_c())


def _event(eid: int) -> AlertEvent:
    async def _c():
        async with SessionLocal() as s:
            return await s.get(AlertEvent, eid)

    return _run(_c())


def _capture_sends(monkeypatch) -> list:
    sent: list = []

    async def fake_send(session, redis, channel, text, **kw):
        sent.append((channel.name, text, kw.get("alert_event_id")))
        return True

    monkeypatch.setattr(notif, "send_to_channel", fake_send)
    return sent


def _domain(make_company, make_project, make_domain, fqdn="card.com", **kw):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    return acme, make_domain(
        proj, fqdn=fqdn, expiry_date=datetime.now(UTC) + timedelta(days=20), **kw
    )


# --- /people ---------------------------------------------------------------------------


def test_people_page_is_admin_only(client, make_user):
    _login(client, make_user, "mgr", Role.manager)
    assert client.get("/people", follow_redirects=False).status_code == 403
    assert client.post("/people", data={"name": "X", "handle": "x"}).status_code == 403


def test_people_crud_and_validation(client, make_user):
    _login(client, make_user, "root", Role.admin)
    ok = client.post(
        "/people",
        data={
            "kind": "person",
            "name": "Вася",
            "handle": "@Vasya",
            "discord_id": f"<@{VASYA_ID}>",
            "telegram_username": "@vasya_ops",
        },
        follow_redirects=False,
    )
    assert ok.status_code == 303
    page = client.get("/people")
    assert "@vasya" in page.text and VASYA_ID in page.text and "@vasya_ops" in page.text

    bad = client.post(
        "/people", data={"kind": "person", "name": "Y", "handle": "y", "discord_id": "abc"}
    )
    assert bad.status_code == 422
    assert "Discord ID" in bad.text
    assert 'value="Y"' in bad.text  # the form keeps what was typed
    dup = client.post("/people", data={"kind": "person", "name": "Z", "handle": "vasya"})
    assert dup.status_code == 422 and "занят" in dup.text

    async def pid():
        async with SessionLocal() as s:
            return await s.scalar(select(Person.id).where(Person.handle == "vasya"))

    vid = _run(pid())
    edit = client.post(
        f"/people/{vid}",
        data={"kind": "person", "name": "Василий", "handle": "vasya", "is_active": "1"},
        follow_redirects=False,
    )
    assert edit.status_code == 303
    assert "Василий" in client.get("/people").text
    client.post(f"/people/{vid}/delete")
    assert "Василий" not in client.get("/people").text


def test_route_form_creates_and_deletes_rule(client, make_user, make_company):
    _login(client, make_user, "root", Role.admin)
    acme = make_company(code="acme")
    vid = _person("vasya")
    resp = client.post(
        "/people/routes",
        data={"scope": f"company:{acme}", "kind": "expiry", "person_id": str(vid)},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    async def routes():
        async with SessionLocal() as s:
            return list((await s.execute(select(AlertRoute))).scalars().all())

    [r] = _run(routes())
    assert (r.company_id, r.project_id, r.kind, r.person_id) == (acme, None, "expiry", vid)
    assert (
        client.post("/people/routes", data={"scope": "global", "kind": "expiry"}).status_code == 422
    )
    client.post(f"/people/routes/{r.id}/delete")
    assert _run(routes()) == []


# --- card ------------------------------------------------------------------------------


def test_card_renders_detailed_sections(client, make_user, make_company, make_project, make_domain):
    _, dom = _domain(
        make_company, make_project, make_domain, auto_renew=False, notes="важный лендинг"
    )
    vid = _person("vasya", discord=VASYA_ID)
    eid = _alert(dom, assignee=vid)
    _login(client, make_user, "root", Role.admin)

    page = client.get(f"/alerts/{eid}")
    assert page.status_code == 200
    for text in (
        "ЧТО СЛУЧИЛОСЬ",
        "Истекает домен",
        "Автопродление",
        "✖ выключено",
        "Что делать:",
        "Продлите вручную",
        "ОТВЕТСТВЕННЫЙ",
        "@vasya",
        "ДОМЕН",
        "важный лендинг",
        "ДОСТАВКА",
        "нет — алерты этого домена никуда не уходят",
        "ЛЕНТА",
        "Сработал",
        "Взял в работу",
        "Закрыть с причиной",
    ):
        assert text in page.text, text


def test_card_for_ns_change_shows_diff(client, make_user, make_company, make_project, make_domain):
    _, dom = _domain(make_company, make_project, make_domain)
    eid = _alert(
        dom,
        kind="ns_change",
        severity="high",
        payload={"old_ns": ["a.ns.com", "b.ns.com"], "new_ns": ["a.ns.com", "evil.ns.net"]},
    )
    _login(client, make_user, "root", Role.admin)
    page = client.get(f"/alerts/{eid}").text
    assert "Добавлены" in page and "evil.ns.net" in page
    assert "Убраны" in page and "b.ns.com" in page
    assert "признак угона" in page


def test_viewer_sees_card_but_cannot_act(
    client, make_user, make_company, make_project, make_domain
):
    acme, dom = _domain(make_company, make_project, make_domain)
    vid = _person("vasya")
    eid = _alert(dom)
    _login(client, make_user, "viewer", Role.viewer, scopes=[{"company_id": acme}])
    page = client.get(f"/alerts/{eid}")
    assert page.status_code == 200 and "Взял в работу" not in page.text
    for path, data in (
        ("assign", {"person_id": str(vid)}),
        ("ack", {}),
        ("comment", {"body": "hi"}),
    ):
        assert (
            client.post(f"/alerts/{eid}/{path}", data=data, follow_redirects=False).status_code
            == 403
        )
    assert _event(eid).assignee_person_id is None


def test_assign_via_card_pings_in_channel(
    client, make_user, make_company, make_project, make_domain, monkeypatch
):
    acme, dom = _domain(make_company, make_project, make_domain)
    vid = _person("vasya", discord=VASYA_ID)
    _channel()
    eid = _alert(dom)
    sent = _capture_sends(monkeypatch)
    _login(client, make_user, "mgr", Role.manager, scopes=[{"company_id": acme}])

    resp = client.post(
        f"/alerts/{eid}/assign", data={"person_id": str(vid), "notify": "1"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/alerts/{eid}?msg=assigned&sent=1"
    assert _event(eid).assignee_person_id == vid
    assert sent[0][0] == "Adera alert" and f"<@{VASYA_ID}>" in sent[0][1]
    assert sent[0][2] == eid  # logged against the alert → shows in «Доставка»
    page = client.get(resp.headers["location"]).text
    assert "упоминание отправлено в каналы: 1" in page


def test_ack_and_comment_and_resolve_via_card(
    client, make_user, make_company, make_project, make_domain, monkeypatch
):
    acme, dom = _domain(make_company, make_project, make_domain)
    vid = _person("vasya", discord=VASYA_ID)
    _channel()
    eid = _alert(dom)
    sent = _capture_sends(monkeypatch)
    mgr = _login(client, make_user, "mgr", Role.manager, scopes=[{"company_id": acme}])

    assert client.post(f"/alerts/{eid}/ack", follow_redirects=False).status_code == 303
    ev = _event(eid)
    assert ev.acked_at is not None and ev.acked_by_id == mgr["id"]
    assert "В РАБОТЕ" in client.get(f"/alerts/{eid}").text

    empty = client.post(f"/alerts/{eid}/comment", data={"body": "  "}, follow_redirects=False)
    assert empty.headers["location"].endswith("msg=empty_comment")

    resp = client.post(
        f"/alerts/{eid}/comment",
        data={"body": "@vasya оплати до пятницы", "to_channel": "1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == f"/alerts/{eid}?msg=commented&sent=1"
    assert f"💬 mgr: <@{VASYA_ID}> оплати до пятницы" in sent[-1][1]
    page = client.get(f"/alerts/{eid}").text
    assert '<span class="mention">@vasya</span>' in page  # highlighted in the timeline

    # Comment text is escaped on the card (no stored XSS).
    client.post(f"/alerts/{eid}/comment", data={"body": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in client.get(f"/alerts/{eid}").text

    client.post(f"/alerts/{eid}/resolve", data={"note": "оплачено"})
    ev = _event(eid)
    assert ev.state == "resolved" and ev.resolution_note == "оплачено"
    assert ev.resolved_by_id == mgr["id"]
    assert vid  # person existed for the mention


def test_actions_respect_scope(client, make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    globex = make_company(code="globex")
    pg = make_project(globex, code="portal")
    dom = make_domain(pg, fqdn="globex.com")
    vid = _person("vasya")
    eid = _alert(dom)
    _login(client, make_user, "mgr", Role.manager, scopes=[{"company_id": acme}])

    for path, data in (
        ("assign", {"person_id": str(vid)}),
        ("ack", {}),
        ("comment", {"body": "x"}),
    ):
        resp = client.post(f"/alerts/{eid}/{path}", data=data, follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/alerts"
    ev = _event(eid)
    assert ev.assignee_person_id is None and ev.acked_at is None

    async def comments():
        async with SessionLocal() as s:
            return list((await s.execute(select(AlertActivity))).scalars().all())

    assert _run(comments()) == []


def test_alert_list_owner_column_and_filter(
    client, make_user, make_company, make_project, make_domain
):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d1 = make_domain(proj, fqdn="owned.com")
    d2 = make_domain(proj, fqdn="orphan.com")
    mgr = make_user(
        login="mgr", password="password123", role=Role.manager, scopes=[{"company_id": acme}]
    )
    me = _person("mgr", user_id=mgr["id"])
    _alert(d1, assignee=me)
    _alert(d2)
    client.post("/login", data={"login": "mgr", "password": "password123"})

    page = client.get("/alerts").text
    assert "КТО" in page and "@mgr" in page
    mine = client.get("/alerts?owner=me").text
    assert "owned.com" in mine and "orphan.com" not in mine
    none = client.get("/alerts?owner=none").text
    assert "orphan.com" in none and "owned.com" not in none
    by_id = client.get(f"/alerts?owner={me}").text
    assert "owned.com" in by_id and "orphan.com" not in by_id
    assert client.get("/alerts?owner=").status_code == 200
