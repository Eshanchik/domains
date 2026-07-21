"""Channel «Отправить сейчас» + alert resend to channel (T48)."""

from __future__ import annotations

import asyncio

from app.db import SessionLocal
from app.models.alert import AlertEvent
from app.models.user import Role
from app.services import notifications as notif


def _run(coro):
    return asyncio.run(coro)


def _login_admin(client, make_user):
    make_user(login="root", password="password123", role=Role.admin)
    client.post("/login", data={"login": "root", "password": "password123"})


def _make_channel(**kw) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ch = await notif.create_channel_typed(
                s,
                type=kw.get("type", "discord"),
                name=kw.get("name", "ch"),
                config={"webhook_url": "https://hooks.example/x"},
                company_id=kw.get("company_id"),
                project_id=kw.get("project_id"),
                is_default=kw.get("is_default", True),
                mode=kw.get("mode", "both"),
                digest_time=None,
                actor_id=None,
            )
            return ch.id

    return _run(_c())


def _make_alert(domain_id: int, kind: str = "expiry", severity: str = "high") -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ev = AlertEvent(
                domain_id=domain_id,
                kind=kind,
                dedupe_key=f"{domain_id}:{kind}",
                severity=severity,
                state="active",
                payload_json={"days": 20, "threshold": 30},
            )
            s.add(ev)
            await s.commit()
            await s.refresh(ev)
            return ev.id

    return _run(_c())


def test_send_now_no_alerts(client, make_user):
    _login_admin(client, make_user)
    cid = _make_channel()
    resp = client.post(f"/channels/{cid}/send-now", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/channels?sent=none"


def test_send_now_dispatches_digest(
    client, make_user, make_company, make_project, make_domain, monkeypatch
):
    _login_admin(client, make_user)
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="soon.com")
    _make_alert(dom, kind="expiry")
    cid = _make_channel(is_default=True)

    sent: list[str] = []

    async def fake_send(session, redis, channel, text):
        sent.append(text)
        return True

    monkeypatch.setattr(notif, "send_to_channel", fake_send)
    resp = client.post(f"/channels/{cid}/send-now", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/channels?sent=ok"
    assert sent and "soon.com" in sent[0]


def test_send_now_requires_admin(client, make_user):
    make_user(login="v", password="password123", role=Role.viewer)
    client.post("/login", data={"login": "v", "password": "password123"})
    cid = _make_channel()
    resp = client.post(f"/channels/{cid}/send-now", follow_redirects=False)
    assert resp.status_code == 403


def test_alert_notify_sends_to_channels(
    client, make_user, make_company, make_project, make_domain, monkeypatch
):
    _login_admin(client, make_user)
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="al.com")
    aid = _make_alert(dom, kind="expiry", severity="high")
    _make_channel(is_default=True, mode="instant")

    sent: list[str] = []

    async def fake_send(session, redis, channel, text):
        sent.append(text)
        return True

    monkeypatch.setattr(notif, "send_to_channel", fake_send)
    resp = client.post(f"/alerts/{aid}/notify", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/alerts/{aid}?notified=1"
    assert sent and "al.com" in sent[0]
