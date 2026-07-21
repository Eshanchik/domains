"""T28: alert detail page + manual resolve."""

from __future__ import annotations

import asyncio

from app.db import SessionLocal
from app.models.alert import AlertEvent
from app.models.user import Role


def _run(coro):
    return asyncio.run(coro)


def _login(client, name, pw):
    return client.post("/login", data={"login": name, "password": pw})


def _make_event(domain_id: int, **kwargs) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ev = AlertEvent(
                domain_id=domain_id,
                kind=kwargs.get("kind", "expiry"),
                dedupe_key=kwargs.get("dedupe_key", f"expiry:{domain_id}:30"),
                severity=kwargs.get("severity", "high"),
                state=kwargs.get("state", "active"),
                payload_json=kwargs.get("payload_json", {"threshold_days": 30, "days_left": 12}),
            )
            s.add(ev)
            await s.commit()
            await s.refresh(ev)
            return ev.id

    return _run(_c())


def _event_state(event_id: int) -> str:
    async def _c() -> str:
        async with SessionLocal() as s:
            return (await s.get(AlertEvent, event_id)).state

    return _run(_c())


def test_alert_detail_renders_fields_and_payload(
    client, make_user, make_company, make_project, make_domain
) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="alerted.com")
    eid = _make_event(dom, kind="ssl", severity="high")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    page = client.get(f"/alerts/{eid}")
    assert page.status_code == 200
    assert "alerted.com" in page.text  # linked domain
    assert "ssl" in page.text  # kind
    assert "threshold_days" in page.text and "days_left" in page.text  # payload rendered

    # The list links to the detail page.
    lst = client.get("/alerts")
    assert f"/alerts/{eid}" in lst.text


def test_alert_list_shows_project_and_age(
    client, make_user, make_company, make_project, make_domain
) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="ag.com")
    _make_event(dom, kind="ssl", severity="high")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    page = client.get("/alerts")
    assert page.status_code == 200
    assert "ag.com" in page.text
    assert "PROJECT" in page.text  # new column header
    assert "web" in page.text  # project name
    assert "ACME" in page.text  # company name
    assert "◆ ACTIVE" in page.text  # state + age column


def test_alert_detail_out_of_scope_redirects(
    client, make_user, make_company, make_project, make_domain
) -> None:
    acme = make_company(code="acme")
    globex = make_company(code="globex")
    pg = make_project(globex, code="portal")
    dom = make_domain(pg, fqdn="globex-alert.com")
    eid = _make_event(dom)
    # Manager scoped to ACME only cannot see a Globex alert.
    make_user(login="mgr", password="password123", role=Role.manager, scopes=[{"company_id": acme}])
    _login(client, "mgr", "password123")

    resp = client.get(f"/alerts/{eid}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/alerts"


def test_alert_resolve_manager_only(
    client, make_user, make_company, make_project, make_domain
) -> None:
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="resolve.com")
    eid = _make_event(dom)

    # Viewer is blocked (Manager+ required) and the event stays active.
    make_user(login="viewer", password="password123", role=Role.viewer)
    _login(client, "viewer", "password123")
    denied = client.post(f"/alerts/{eid}/resolve", follow_redirects=False)
    assert denied.status_code == 403
    assert _event_state(eid) == "active"

    # Admin resolves it.
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")
    resp = client.post(f"/alerts/{eid}/resolve", follow_redirects=False)
    assert resp.status_code == 303
    assert _event_state(eid) == "resolved"


def test_alert_list_filters(client, make_user, make_company, make_project, make_domain):
    acme = make_company(code="acme")
    other = make_company(code="other")
    pa = make_project(acme, code="web")
    po = make_project(other, code="web")
    da = make_domain(pa, fqdn="a-ssl.com")
    da2 = make_domain(pa, fqdn="a-exp.com")
    do = make_domain(po, fqdn="o-ssl.com")
    _make_event(da, kind="ssl", severity="high")
    _make_event(da2, kind="expiry", severity="medium")
    _make_event(do, kind="ssl", severity="high")
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")

    # by company
    r = client.get(f"/alerts?company_id={acme}")
    assert "a-ssl.com" in r.text and "a-exp.com" in r.text and "o-ssl.com" not in r.text
    # by kind
    r = client.get("/alerts?kind=ssl")
    assert "a-ssl.com" in r.text and "o-ssl.com" in r.text and "a-exp.com" not in r.text
    # by severity
    r = client.get("/alerts?severity=medium")
    assert "a-exp.com" in r.text and "a-ssl.com" not in r.text
    # by project
    r = client.get(f"/alerts?project_id={po}")
    assert "o-ssl.com" in r.text and "a-ssl.com" not in r.text
    # blank params tolerated (no 422)
    assert client.get("/alerts?company_id=&project_id=&severity=&kind=").status_code == 200
