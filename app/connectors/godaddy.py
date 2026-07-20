"""GoDaddy connector (SPEC FR-RG-2, Phase 2 / T18).

Calls the GoDaddy Domains API (``GET /v1/domains``, JSON) with marker pagination.
Auth is an ``sso-key {key}:{secret}`` header. ``_fetch_page`` is the network seam.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from app.connectors.base import ConnectorError, RegistrarConnector, RegistrarDomain

API_URL = "https://api.godaddy.com/v1/domains"
PAGE_SIZE = 100


class GoDaddyConnector(RegistrarConnector):
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self._client = client

    def _headers(self) -> dict:
        return {
            "Authorization": f"sso-key {self.api_key}:{self.api_secret}",
            "Accept": "application/json",
        }

    async def _fetch_page(self, marker: str | None) -> list[dict]:
        params = {"limit": PAGE_SIZE}
        if marker:
            params["marker"] = marker
        owns = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            resp = await client.get(API_URL, params=params, headers=self._headers(), timeout=20.0)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"godaddy request failed: {exc}") from exc
        finally:
            if owns:
                await client.aclose()

        if resp.status_code in (401, 403):
            raise ConnectorError(f"godaddy auth error ({resp.status_code})")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ConnectorError(f"godaddy status {resp.status_code}")
        if resp.status_code >= 400:
            raise ConnectorError(f"godaddy status {resp.status_code}")
        data = resp.json()
        if not isinstance(data, list):
            raise ConnectorError("godaddy: unexpected response shape")
        return data

    async def list_domains(self) -> list[RegistrarDomain]:
        out: list[RegistrarDomain] = []
        marker: str | None = None
        while True:
            batch = await self._fetch_page(marker)
            for item in batch:
                name = item.get("domain")
                if not name:
                    continue
                out.append(
                    RegistrarDomain(
                        fqdn=name,
                        expiry_date=_parse_date(item.get("expires")),
                        auto_renew=bool(item.get("renewAuto", False)),
                    )
                )
            if len(batch) < PAGE_SIZE:
                break
            marker = batch[-1].get("domain")
            if not marker:
                break
        return out


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
