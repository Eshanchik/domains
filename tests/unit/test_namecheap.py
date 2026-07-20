"""Namecheap XML parsing, pagination, and auth-error handling."""

from __future__ import annotations

import asyncio

import pytest

from app.connectors.base import ConnectorError
from app.connectors.namecheap import PAGE_SIZE, NamecheapConnector, _parse_page

OK_XML = """<?xml version="1.0" encoding="utf-8"?>
<ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
  <CommandResponse>
    <DomainGetListResult>
      <Domain ID="1" Name="example.com" Expires="01/15/2027" AutoRenew="true" />
      <Domain ID="2" Name="test.org" Expires="06/30/2026" AutoRenew="false" />
    </DomainGetListResult>
  </CommandResponse>
</ApiResponse>"""

ERROR_XML = """<?xml version="1.0" encoding="utf-8"?>
<ApiResponse Status="ERROR" xmlns="http://api.namecheap.com/xml.response">
  <Errors><Error Number="1011102">API Key is invalid</Error></Errors>
</ApiResponse>"""


def test_parse_ok() -> None:
    domains = _parse_page(OK_XML)
    assert [d.fqdn for d in domains] == ["example.com", "test.org"]
    assert domains[0].expiry_date.year == 2027
    assert domains[0].auto_renew is True
    assert domains[1].auto_renew is False


def test_parse_error_raises() -> None:
    with pytest.raises(ConnectorError) as exc:
        _parse_page(ERROR_XML)
    assert "API Key is invalid" in str(exc.value)


def _page_xml(n: int) -> str:
    rows = "".join(
        f'<Domain ID="{i}" Name="d{i}.com" Expires="01/01/2027" AutoRenew="false" />'
        for i in range(n)
    )
    return (
        '<ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">'
        f"<CommandResponse><DomainGetListResult>{rows}</DomainGetListResult></CommandResponse>"
        "</ApiResponse>"
    )


def test_pagination_walks_all_pages(monkeypatch) -> None:
    conn = NamecheapConnector(api_user="u", api_key="k", username="u", client_ip="1.2.3.4")
    pages = {1: _page_xml(PAGE_SIZE), 2: _page_xml(3)}  # full page then a short page

    async def fake_fetch(page: int) -> str:
        return pages[page]

    monkeypatch.setattr(conn, "_fetch_page", fake_fetch)
    domains = asyncio.run(conn.list_domains())
    assert len(domains) == PAGE_SIZE + 3  # stopped after the short page
