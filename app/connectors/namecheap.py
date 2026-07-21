"""Namecheap connector (SPEC FR-RG-3).

Calls ``namecheap.domains.getList`` (XML API) with pagination. Requires the server
IP to be whitelisted in the Namecheap account. ``_fetch_page`` is the network seam
tests patch/mocked via respx.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

import httpx

from app.connectors.base import ConnectorError, RegistrarConnector, RegistrarDomain, TldPrice

API_URL = "https://api.namecheap.com/xml.response"
_NS = "http://api.namecheap.com/xml.response"
PAGE_SIZE = 100


class NamecheapConnector(RegistrarConnector):
    def __init__(
        self,
        *,
        api_user: str,
        api_key: str,
        username: str,
        client_ip: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_user = api_user
        self.api_key = api_key
        self.username = username
        self.client_ip = client_ip
        self._client = client

    def _params(self, page: int) -> dict:
        return {
            "ApiUser": self.api_user,
            "ApiKey": self.api_key,
            "UserName": self.username,
            "ClientIp": self.client_ip,
            "Command": "namecheap.domains.getList",
            "Page": str(page),
            "PageSize": str(PAGE_SIZE),
        }

    async def _fetch_page(self, page: int) -> str:
        owns = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            resp = await client.get(API_URL, params=self._params(page), timeout=20.0)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            raise ConnectorError(f"namecheap request failed: {exc}") from exc
        finally:
            if owns:
                await client.aclose()

    async def list_domains(self) -> list[RegistrarDomain]:
        domains: list[RegistrarDomain] = []
        page = 1
        while True:
            xml = await self._fetch_page(page)
            batch = _parse_page(xml)
            domains.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        return domains

    def _pricing_params(self) -> dict:
        return {
            "ApiUser": self.api_user,
            "ApiKey": self.api_key,
            "UserName": self.username,
            "ClientIp": self.client_ip,
            "Command": "namecheap.users.getPricing",
            "ProductType": "DOMAIN",
            "ActionName": "RENEW",
        }

    async def _fetch_pricing(self) -> str:
        owns = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            resp = await client.get(API_URL, params=self._pricing_params(), timeout=20.0)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            raise ConnectorError(f"namecheap pricing request failed: {exc}") from exc
        finally:
            if owns:
                await client.aclose()

    async def get_renewal_prices(self) -> dict[str, TldPrice]:
        """Return the 1-year RENEW price per TLD (``users.getPricing``)."""
        return _parse_pricing(await self._fetch_pricing())


def _tag(name: str) -> str:
    return f"{{{_NS}}}{name}"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_page(xml_text: str) -> list[RegistrarDomain]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ConnectorError(f"namecheap: bad XML ({exc})") from exc

    status = root.get("Status")
    if status and status.upper() == "ERROR":
        errors = root.find(_tag("Errors"))
        msg = "unknown error"
        if errors is not None and len(errors):
            msg = (errors[0].text or "").strip() or msg
        raise ConnectorError(f"namecheap API error: {msg}")

    result = root.find(f".//{_tag('DomainGetListResult')}")
    if result is None:
        return []

    out: list[RegistrarDomain] = []
    for dom in result.findall(_tag("Domain")):
        name = dom.get("Name")
        if not name:
            continue
        out.append(
            RegistrarDomain(
                fqdn=name,
                expiry_date=_parse_date(dom.get("Expires")),
                auto_renew=(dom.get("AutoRenew", "").lower() == "true"),
            )
        )
    return out


def _check_error(root: ET.Element) -> None:
    if (root.get("Status") or "").upper() == "ERROR":
        errors = root.find(_tag("Errors"))
        msg = "unknown error"
        if errors is not None and len(errors):
            msg = (errors[0].text or "").strip() or msg
        raise ConnectorError(f"namecheap API error: {msg}")


def _price_amount(price_el: ET.Element) -> Decimal | None:
    raw = price_el.get("YourPrice") or price_el.get("Price")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _parse_pricing(xml_text: str) -> dict[str, TldPrice]:
    """Extract 1-year RENEW price per TLD from a ``users.getPricing`` response."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ConnectorError(f"namecheap: bad pricing XML ({exc})") from exc
    _check_error(root)

    prices: dict[str, TldPrice] = {}
    for ptype in root.iter(_tag("ProductType")):
        if (ptype.get("Name") or "").lower() != "domains":
            continue
        for category in ptype.findall(_tag("ProductCategory")):
            if (category.get("Name") or "").lower() != "renew":
                continue
            for product in category.findall(_tag("Product")):
                tld = (product.get("Name") or "").lower().lstrip(".")
                if not tld:
                    continue
                for price_el in product.findall(_tag("Price")):
                    if (
                        price_el.get("Duration") != "1"
                        or (price_el.get("DurationType") or "").upper() != "YEAR"
                    ):
                        continue
                    amount = _price_amount(price_el)
                    if amount is None:
                        continue
                    prices[tld] = TldPrice(
                        tld=tld, price=amount, currency=price_el.get("Currency") or "USD"
                    )
                    break
    return prices
