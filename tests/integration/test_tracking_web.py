"""T99: «Трекинг» tab and the Google-protected status page for company employees."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models.audit import AuditLog
from app.models.domain import Domain
from app.models.healthcheck import HealthCheck, HealthCheckResult
from app.models.user import Role
from app.services import companies as companies_svc
from app.services import google_oauth
from app.services import tracking as t
from app.services.google_oauth import GoogleIdentity


def _run(coro):
    return asyncio.run(coro)


def _login(client, make_user, login, role, **kw):
    u = make_user(login=login, password="password123", role=role, **kw)
    client.post("/login", data={"login": login, "password": "password123"})
    return u


def _tracker(domain_id: int, *, ok: bool = True) -> None:
    async def _c():
        async with SessionLocal() as s:
            d = await s.get(Domain, domain_id)
            d.tags = [*d.tags, await companies_svc.get_or_create_tag(s, "tracking")]
            hc = HealthCheck(
                domain_id=domain_id,
                url=f"https://{d.fqdn}/click?pid=1&offer_id=625",
                expected_statuses="200-399",
                state="up" if ok else "down",
                consecutive_failures=0 if ok else 5,
                last_checked_at=datetime.now(UTC),
            )
            s.add(hc)
            await s.flush()
            s.add(
                HealthCheckResult(
                    healthcheck_id=hc.id,
                    ok=ok,
                    status_code=302 if ok else None,
                    latency_ms=90 if ok else None,
                    error=None if ok else "request failed: ",
                    checked_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            await s.commit()

    _run(_c())


@pytest.fixture
def two_companies(make_company, make_project, make_domain):
    adera = make_company(code="adera", name="Adera")
    gt1 = make_company(code="gt1", name="GT1")
    pa = make_project(adera, code="ad")
    pg = make_project(gt1, code="kgb")
    a_up = make_domain(pa, fqdn="adera-up.com")
    a_down = make_domain(pa, fqdn="adera-down.com")
    g = make_domain(pg, fqdn="gt1-track.com")
    _tracker(a_up)
    _tracker(a_down, ok=False)
    _tracker(g)
    return {"adera": adera, "gt1": gt1, "pa": pa, "pg": pg, "a_up": a_up}


# --- internal tab -----------------------------------------------------------------------


def test_tab_shows_board_to_viewer_scoped(client, make_user, two_companies):
    _login(client, make_user, "v", Role.viewer, scopes=[{"company_id": two_companies["adera"]}])
    page = client.get("/tracking")
    assert page.status_code == 200
    assert "ТРЕКИНГ ДОМЕНОВ" in page.text and 'href="/tracking"' in page.text  # nav item
    assert "adera-up.com" in page.text and "adera-down.com" in page.text
    assert "gt1-track.com" not in page.text  # outside the viewer's scope
    assert "лежит" in page.text and "нет соединения" in page.text
    assert "Добавить в трекинг" not in page.text  # viewer can't manage

    part = client.get("/tracking?partial=1")
    assert "ТРЕКЕРЫ" in part.text and "ТРЕКИНГ ДОМЕНОВ" not in part.text  # fragment only

    for path in ("/tracking/add", f"/tracking/{two_companies['a_up']}/recheck"):
        assert client.post(path, data={"fqdns": "x.com"}, follow_redirects=False).status_code == 403


def test_manager_adds_rechecks_and_removes(
    client, make_user, make_company, make_project, make_domain
):
    acme = make_company(code="acme")
    p = make_project(acme, code="web")
    d = make_domain(p, fqdn="fresh.com")
    _login(client, make_user, "m", Role.manager, scopes=[{"company_id": acme}])

    resp = client.post(
        "/tracking/add",
        data={"fqdns": "fresh.com\nghost.com", "url_template": "https://{fqdn}/click"},
    )
    assert resp.status_code == 200
    assert "Добавлено в трекинг: 1" in resp.text and "ghost.com" in resp.text
    assert "fresh.com" in client.get("/tracking").text

    bad = client.post("/tracking/add", data={"fqdns": "fresh.com", "url_template": "https://x/"})
    assert bad.status_code == 422 and "{fqdn}" in bad.text

    r = client.post(f"/tracking/{d}/recheck", follow_redirects=False)
    assert r.headers["location"] == "/tracking?msg=recheck1"
    r = client.post(f"/tracking/{d}/remove", follow_redirects=False)
    assert r.headers["location"] == "/tracking?msg=removed"
    assert "fresh.com" not in client.get("/tracking").text


def test_access_rules_admin_only(client, make_user, two_companies):
    _login(client, make_user, "m", Role.manager, scopes=[{"company_id": two_companies["adera"]}])
    denied = client.post("/tracking/access", data={"email_domain": "adera.agency"})
    assert denied.status_code == 403

    _login(client, make_user, "root", Role.admin)
    client.post(
        "/tracking/access",
        data={"email_domain": "@adera.agency", "company_id": str(two_companies["adera"])},
    )
    page = client.get("/tracking").text
    assert "@adera.agency" in page and "компании" in page.lower()
    client.post("/tracking/access/delete", data={"email_domain": "adera.agency"})
    assert "Доступ никому не открыт" in client.get("/tracking").text


# --- status page ------------------------------------------------------------------------


@pytest.fixture
def oauth_on(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_redirect_uri", "https://dg.test/auth/google/callback")


def _identity(monkeypatch, email, hd, verified=True):
    async def fake_exchange(code, redirect_uri, *, client=None):
        return GoogleIdentity(email=email, email_verified=verified, hd=hd)

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange)


def _allow(company_id):
    async def _c():
        async with SessionLocal() as s:
            await t.add_access_rule(
                s, email_domain="adera.agency", company_id=company_id, actor_id=None
            )

    _run(_c())


def _status_login(client, monkeypatch, email, hd, verified=True):
    _identity(monkeypatch, email, hd, verified)
    start = client.get("/status/login/google", follow_redirects=False)
    state = client.cookies.get("dg_oauth_state")
    return start, client.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)


def test_status_page_requires_login(client, oauth_on):
    page = client.get("/status/tracking")
    assert page.status_code == 200 and "Войти через Google" in page.text
    assert "adera" not in page.text.lower()
    part = client.get("/status/tracking?partial=1")
    assert part.status_code == 401 and part.headers.get("HX-Refresh") == "true"


def test_status_login_disabled_without_google(client):
    assert "ещё не настроен" in client.get("/status/tracking").text
    assert client.get("/status/login/google").status_code == 503


def test_employee_signs_in_and_sees_only_company_trackers(
    client, oauth_on, monkeypatch, two_companies
):
    _allow(two_companies["adera"])
    start, cb = _status_login(client, monkeypatch, "ivan@adera.agency", "adera.agency")
    assert "accounts.google.com" in start.headers["location"]
    assert "hd=adera.agency" in start.headers["location"]  # account-chooser hint
    assert cb.status_code == 303 and cb.headers["location"] == "/status/tracking"
    assert client.cookies.get("dg_status")

    page = client.get("/status/tracking")
    assert page.status_code == 200
    assert "adera-up.com" in page.text and "adera-down.com" in page.text
    assert "gt1-track.com" not in page.text
    assert "ivan@adera.agency" in page.text and "Выйти" in page.text
    assert "/domains/" not in page.text  # no links into DomainGuard

    # The status session grants nothing else.
    assert client.get("/domains", follow_redirects=False).status_code in (303, 307)
    assert client.get("/tracking", follow_redirects=False).status_code in (303, 307)

    async def audits():
        async with SessionLocal() as s:
            return await s.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "status_login")
            )

    assert _run(audits()) == 1

    client.post("/status/logout")
    assert "Войти через Google" in client.get("/status/tracking").text


@pytest.mark.parametrize(
    ("email", "hd", "verified"),
    [
        ("ivan@adera.agency", None, True),  # personal Google account on a work address
        ("ivan@gmail.com", None, True),
        ("ivan@adera.agency", "adera.agency", False),
        ("ivan@evil.com", "evil.com", True),
    ],
)
def test_status_login_refuses_non_workspace_accounts(
    client, oauth_on, monkeypatch, two_companies, email, hd, verified
):
    _allow(two_companies["adera"])
    _, cb = _status_login(client, monkeypatch, email, hd, verified)
    assert cb.status_code == 403 and "Доступ только" in cb.text
    assert not client.cookies.get("dg_status")


def test_removing_the_rule_revokes_open_sessions(client, oauth_on, monkeypatch, two_companies):
    _allow(two_companies["adera"])
    _status_login(client, monkeypatch, "ivan@adera.agency", "adera.agency")
    assert "adera-up.com" in client.get("/status/tracking").text

    async def revoke():
        async with SessionLocal() as s:
            await t.delete_access_rule(s, email_domain="adera.agency", actor_id=None)

    _run(revoke())
    assert "Войти через Google" in client.get("/status/tracking").text


def test_status_purpose_never_creates_a_domainguard_session(
    client, oauth_on, monkeypatch, make_user, two_companies
):
    # Even if a DomainGuard user has this e-mail, the status flow only opens the status page.
    make_user(login="ivan", email="ivan@adera.agency", role=Role.admin)
    _allow(two_companies["adera"])
    _, cb = _status_login(client, monkeypatch, "ivan@adera.agency", "adera.agency")
    assert cb.headers["location"] == "/status/tracking"
    assert not client.cookies.get("dg_session")

    # A normal Google login clears any stale purpose cookie, so it can't be hijacked.
    resp = client.get("/auth/google/login", follow_redirects=False)
    cleared = [h for h in resp.headers.get_list("set-cookie") if h.startswith("dg_oauth_purpose=")]
    assert cleared and "max-age=0" in cleared[0].lower()


def test_domainguard_user_sees_status_page_with_own_scope(client, make_user, two_companies):
    _login(client, make_user, "g", Role.viewer, scopes=[{"company_id": two_companies["gt1"]}])
    page = client.get("/status/tracking")
    assert "gt1-track.com" in page.text and "adera-up.com" not in page.text
