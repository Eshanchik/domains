"""Namecheap XML parsing, pagination, and auth-error handling."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.connectors.base import ConnectorError
from app.connectors.namecheap import PAGE_SIZE, NamecheapConnector, _parse_page, _parse_pricing

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


PRICING_XML = """<?xml version="1.0" encoding="utf-8"?>
<ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
  <CommandResponse Type="namecheap.users.getPricing">
    <UserGetPricingResult>
      <ProductType Name="domains">
        <ProductCategory Name="register">
          <Product Name="com">
            <Price Duration="1" DurationType="YEAR" Price="9.06" YourPrice="9.06" Currency="USD" />
          </Product>
        </ProductCategory>
        <ProductCategory Name="renew">
          <Product Name="com">
            <Price Duration="1" DurationType="YEAR" Price="11.48" YourPrice="10.98" Currency="USD"/>
            <Price Duration="2" DurationType="YEAR" Price="22.00" YourPrice="21.00" Currency="USD"/>
          </Product>
          <Product Name="io">
            <Price Duration="1" DurationType="YEAR" Price="34.98" Currency="USD" />
          </Product>
        </ProductCategory>
      </ProductType>
    </UserGetPricingResult>
  </CommandResponse>
</ApiResponse>"""


def test_parse_pricing_picks_one_year_renew() -> None:
    prices = _parse_pricing(PRICING_XML)
    assert set(prices) == {"com", "io"}
    # YourPrice preferred over Price; only the 1-year row is used.
    assert prices["com"].price == Decimal("10.98")
    assert prices["com"].currency == "USD"
    # Falls back to Price when YourPrice is absent.
    assert prices["io"].price == Decimal("34.98")


def test_parse_pricing_error_raises() -> None:
    import pytest as _pytest

    with _pytest.raises(ConnectorError):
        _parse_pricing(ERROR_XML)


def test_get_renewal_prices_uses_fetch_seam(monkeypatch) -> None:
    conn = NamecheapConnector(api_user="u", api_key="k", username="u", client_ip="1.2.3.4")

    async def fake_fetch() -> str:
        return PRICING_XML

    monkeypatch.setattr(conn, "_fetch_pricing", fake_fetch)
    prices = asyncio.run(conn.get_renewal_prices())
    assert prices["com"].price == Decimal("10.98")
