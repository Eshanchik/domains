"""RDAP parsing and query error handling."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.checks.rdap import (
    RdapError,
    RdapNotFound,
    base_for_tld,
    parse_rdap,
    query_domain,
)

RDAP_PAYLOAD = {
    "events": [
        {"eventAction": "expiration", "eventDate": "2027-05-01T00:00:00Z"},
        {"eventAction": "registration", "eventDate": "2020-05-01T00:00:00Z"},
    ],
    "status": ["client transfer prohibited"],
    "nameservers": [{"ldhName": "NS1.example.com"}, {"ldhName": "ns2.example.com"}],
    "entities": [
        {"roles": ["registrant"], "vcardArray": ["vcard", [["fn", {}, "text", "John Doe"]]]}
    ],
}

BOOTSTRAP = {"services": [[["com", "net"], ["https://rdap.example/"]]]}


def test_parse_rdap_extracts_fields() -> None:
    data = parse_rdap(RDAP_PAYLOAD)
    assert data.expiry_date is not None and data.expiry_date.year == 2027
    assert data.registration_date.year == 2020
    assert data.statuses == ["client transfer prohibited"]
    assert data.nameservers == ["ns1.example.com", "ns2.example.com"]
    assert data.registrant == "John Doe"


def test_base_for_tld() -> None:
    assert base_for_tld(BOOTSTRAP, "com") == "https://rdap.example/"
    assert base_for_tld(BOOTSTRAP, "org") is None


@respx.mock
async def test_query_domain_success() -> None:
    respx.get("https://rdap.example/domain/example.com").respond(json=RDAP_PAYLOAD)
    async with httpx.AsyncClient() as client:
        payload = await query_domain(client, "https://rdap.example/", "example.com")
    assert payload["status"] == ["client transfer prohibited"]


@respx.mock
async def test_query_domain_404_raises_notfound() -> None:
    respx.get("https://rdap.example/domain/missing.com").respond(404)
    async with httpx.AsyncClient() as client:
        with pytest.raises(RdapNotFound):
            await query_domain(client, "https://rdap.example/", "missing.com")


@respx.mock
@pytest.mark.parametrize("code", [429, 503])
async def test_query_domain_transient_raises_rdaperror(code: int) -> None:
    respx.get("https://rdap.example/domain/x.com").respond(code)
    async with httpx.AsyncClient() as client:
        with pytest.raises(RdapError):
            await query_domain(client, "https://rdap.example/", "x.com")


@respx.mock
async def test_query_domain_timeout_raises_rdaperror() -> None:
    respx.get("https://rdap.example/domain/slow.com").mock(side_effect=httpx.TimeoutException("t"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(RdapError):
            await query_domain(client, "https://rdap.example/", "slow.com")
