"""WHOIS fallback parsing."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.checks import whois as whois_mod
from app.checks.whois import WhoisError, query_whois


async def test_query_whois_parses_record(monkeypatch) -> None:
    record = SimpleNamespace(
        expiration_date=[datetime(2028, 1, 1), datetime(2028, 6, 1)],
        creation_date=datetime(2019, 1, 1),
        status="ok",
        name_servers=["NS1.example.com", "ns1.example.com", "ns2.example.com"],
    )
    monkeypatch.setattr(whois_mod, "_whois_lookup", lambda fqdn: record)

    data = await query_whois("example.com")
    assert data.expiry_date.year == 2028
    assert data.registration_date.year == 2019
    assert data.statuses == ["ok"]
    assert data.nameservers == ["ns1.example.com", "ns2.example.com"]  # deduped, lowercased


async def test_query_whois_raises_on_lookup_error(monkeypatch) -> None:
    def _boom(fqdn):
        raise OSError("connection refused")

    monkeypatch.setattr(whois_mod, "_whois_lookup", _boom)
    with pytest.raises(WhoisError):
        await query_whois("example.com")


async def test_query_whois_raises_on_empty(monkeypatch) -> None:
    monkeypatch.setattr(whois_mod, "_whois_lookup", lambda fqdn: None)
    with pytest.raises(WhoisError):
        await query_whois("example.com")
