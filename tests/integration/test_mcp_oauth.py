"""MCP OAuth consent flow: login gate, permission gate, code issuance, login next."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

from app.mcp import oauth_store as store
from app.models.user import Role


def _run(coro):
    return asyncio.run(coro)


def _authreq(rid: str, redirect_uri: str = "https://claude.ai/callback") -> None:
    _run(
        store.put_authreq(
            rid,
            {
                "client_id": "test-client",
                "redirect_uri": redirect_uri,
                "redirect_uri_provided_explicitly": True,
                "scopes": ["mcp"],
                "state": "st4te",
                "code_challenge": "abc123challenge",
                "resource": None,
            },
        )
    )


def _login(client, login: str, password: str):
    return client.post("/login", data={"login": login, "password": password})


def test_consent_requires_login(client):
    _authreq("rid-anon")
    resp = client.get("/oauth/consent?rid=rid-anon", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login?next=")


def test_consent_unknown_rid_errors(client, make_user):
    make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")
    resp = client.get("/oauth/consent?rid=does-not-exist")
    assert resp.status_code == 400
    assert "не найден" in resp.text


def test_consent_denied_for_user_without_permission(client, make_user):
    make_user(login="viewer1", password="password123", role=Role.viewer)  # mcp_allowed=False
    _login(client, "viewer1", "password123")
    _authreq("rid-deny")
    resp = client.get("/oauth/consent?rid=rid-deny")
    assert resp.status_code == 200
    assert "нет прав на MCP" in resp.text  # only a cancel button, no approve


def test_consent_approve_issues_code(client, make_user):
    info = make_user(login="root", password="password123", role=Role.admin)
    _login(client, "root", "password123")
    _authreq("rid-ok")

    resp = client.post(
        "/oauth/consent",
        data={"rid": "rid-ok", "decision": "approve"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("https://claude.ai/callback")
    qs = parse_qs(urlparse(loc).query)
    assert qs["state"] == ["st4te"]
    code = qs["code"][0]

    stored = _run(store.get_code(code))
    assert stored is not None
    assert stored.subject == str(info["id"])  # code bound to the approving user
    assert stored.client_id == "test-client"


def test_consent_deny_redirects_with_error(client, make_user):
    make_user(login="root2", password="password123", role=Role.admin)
    _login(client, "root2", "password123")
    _authreq("rid-no")

    resp = client.post(
        "/oauth/consent",
        data={"rid": "rid-no", "decision": "deny"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    qs = parse_qs(urlparse(resp.headers["location"]).query)
    assert qs["error"] == ["access_denied"]


def test_login_next_redirects_back(client, make_user):
    make_user(login="root3", password="password123", role=Role.admin)
    resp = client.post(
        "/login",
        data={"login": "root3", "password": "password123", "next": "/oauth/consent?rid=xyz"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/oauth/consent?rid=xyz"


def test_login_next_rejects_external(client, make_user):
    make_user(login="root4", password="password123", role=Role.admin)
    resp = client.post(
        "/login",
        data={"login": "root4", "password": "password123", "next": "https://evil.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"  # open-redirect blocked
