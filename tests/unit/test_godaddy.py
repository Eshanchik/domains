"""GoDaddy connector: parse, pagination, auth error."""

from __future__ import annotations

import asyncio

import pytest
import respx

from app.connectors.base import ConnectorError
from app.connectors.godaddy import API_URL, PAGE_SIZE, GoDaddyConnector


def _conn() -> GoDaddyConnector:
    return GoDaddyConnector(api_key="k", api_secret="s")


@respx.mock
async def test_list_domains_parses() -> None:
    respx.get(API_URL).respond(
        json=[
            {"domain": "example.com", "expires": "2027-05-01T00:00:00Z", "renewAuto": True},
            {"domain": "test.org", "expires": "2026-06-30T00:00:00Z", "renewAuto": False},
        ]
    )
    domains = await _conn().list_domains()
    assert [d.fqdn for d in domains] == ["example.com", "test.org"]
    assert domains[0].expiry_date.year == 2027
    assert domains[0].auto_renew is True
    assert domains[1].auto_renew is False


@respx.mock
async def test_auth_error_raises() -> None:
    respx.get(API_URL).respond(403)
    with pytest.raises(ConnectorError):
        await _conn().list_domains()


def test_pagination_walks_markers(monkeypatch) -> None:
    conn = _conn()
    page1 = [{"domain": f"d{i}.com", "expires": None, "renewAuto": False} for i in range(PAGE_SIZE)]
    page2 = [{"domain": "last.com", "expires": None, "renewAuto": False}]
    calls: list[str | None] = []

    async def fake_fetch(marker):
        calls.append(marker)
        return page1 if marker is None else page2

    monkeypatch.setattr(conn, "_fetch_page", fake_fetch)
    domains = asyncio.run(conn.list_domains())
    assert len(domains) == PAGE_SIZE + 1
    assert calls == [None, f"d{PAGE_SIZE - 1}.com"]  # marker = last domain of page 1


@respx.mock
async def test_sends_sso_key_header() -> None:
    route = respx.get(API_URL).respond(json=[])
    await _conn().list_domains()
    assert route.calls.last.request.headers["Authorization"] == "sso-key k:s"
