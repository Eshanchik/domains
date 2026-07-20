"""RDAP client: IANA bootstrap (cached) + query + parse (SPEC FR-CK-1).

RDAP is the primary source for expiry/statuses/nameservers/registrant. Server
discovery uses the IANA bootstrap file (https://data.iana.org/rdap/dns.json),
cached in Redis. WHOIS is the fallback (see ``app.checks.whois``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import redis.asyncio as aioredis

IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
_BOOTSTRAP_CACHE_KEY = "rdap:bootstrap"
_BOOTSTRAP_TTL = 24 * 3600


class RdapError(Exception):
    """Transient RDAP failure (timeout, 429, 5xx) — caller marks data stale."""


class RdapNotFound(Exception):
    """RDAP returned 404 — no record for this domain via RDAP."""


@dataclass
class RdapData:
    expiry_date: datetime | None = None
    registration_date: datetime | None = None
    updated_date: datetime | None = None
    statuses: list[str] = field(default_factory=list)
    nameservers: list[str] = field(default_factory=list)
    registrant: str | None = None


async def load_bootstrap(client: httpx.AsyncClient, redis: aioredis.Redis) -> dict:
    """Return the IANA bootstrap document, cached in Redis for a day."""
    cached = await redis.get(_BOOTSTRAP_CACHE_KEY)
    if cached:
        return json.loads(cached)
    resp = await client.get(IANA_BOOTSTRAP_URL, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    await redis.set(_BOOTSTRAP_CACHE_KEY, json.dumps(data), ex=_BOOTSTRAP_TTL)
    return data


def base_for_tld(bootstrap: dict, tld: str) -> str | None:
    """Find the RDAP base URL serving ``tld`` from the bootstrap services list."""
    tld = tld.lower()
    for entry in bootstrap.get("services", []):
        tlds, urls = entry[0], entry[1]
        if tld in [t.lower() for t in tlds] and urls:
            base = urls[0]
            return base if base.endswith("/") else base + "/"
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_rdap(payload: dict) -> RdapData:
    """Extract the fields we track from an RDAP domain response."""
    data = RdapData(statuses=list(payload.get("status", []) or []))

    for event in payload.get("events", []) or []:
        action = event.get("eventAction")
        parsed = _parse_date(event.get("eventDate"))
        if action == "expiration":
            data.expiry_date = parsed
        elif action == "registration":
            data.registration_date = parsed
        elif action in ("last changed", "last update of RDAP database"):
            data.updated_date = parsed

    for ns in payload.get("nameservers", []) or []:
        name = ns.get("ldhName") or ns.get("unicodeName")
        if name:
            data.nameservers.append(name.lower())

    for entity in payload.get("entities", []) or []:
        roles = entity.get("roles", []) or []
        if "registrant" in roles:
            data.registrant = _vcard_fn(entity)
            break
    return data


def _vcard_fn(entity: dict) -> str | None:
    vcard = entity.get("vcardArray")
    if not vcard or len(vcard) < 2:
        return None
    for item in vcard[1]:
        if item and item[0] == "fn":
            return item[3] if len(item) > 3 else None
    return None


async def query_domain(
    client: httpx.AsyncClient, base: str, fqdn: str, *, timeout: float = 10.0
) -> dict:
    """Fetch the RDAP record for ``fqdn``. Raises RdapNotFound/RdapError."""
    url = f"{base}domain/{fqdn}"
    try:
        resp = await client.get(url, timeout=timeout, headers={"Accept": "application/rdap+json"})
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise RdapError(f"rdap request failed: {exc}") from exc

    if resp.status_code == 404:
        raise RdapNotFound(fqdn)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RdapError(f"rdap status {resp.status_code}")
    if resp.status_code >= 400:
        raise RdapError(f"rdap status {resp.status_code}")
    return resp.json()
