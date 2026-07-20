"""WHOIS fallback for expiry (SPEC FR-CK-1).

Used only when RDAP has no answer. ``python-whois`` is synchronous and does socket
IO, so it runs in a thread. ``_whois_lookup`` is the seam tests patch.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.checks.rdap import RdapData


class WhoisError(Exception):
    """WHOIS lookup failed — caller marks data stale."""


def _whois_lookup(fqdn: str) -> Any:  # pragma: no cover - exercised via mocks
    import whois  # imported lazily so tests can patch without the socket dependency

    return whois.whois(fqdn)


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _as_datetime(value: Any) -> datetime | None:
    value = _first(value)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


async def query_whois(fqdn: str) -> RdapData:
    """Look up ``fqdn`` via WHOIS and normalize into RdapData. Raises WhoisError."""
    try:
        record = await asyncio.to_thread(_whois_lookup, fqdn)
    except Exception as exc:  # noqa: BLE001 — normalize any lookup failure
        raise WhoisError(str(exc)) from exc

    if record is None:
        raise WhoisError("empty whois response")

    data = RdapData()
    data.expiry_date = _as_datetime(getattr(record, "expiration_date", None))
    data.registration_date = _as_datetime(getattr(record, "creation_date", None))
    data.updated_date = _as_datetime(getattr(record, "updated_date", None))

    status = getattr(record, "status", None)
    if isinstance(status, list):
        data.statuses = [str(s) for s in status]
    elif status:
        data.statuses = [str(status)]

    ns = getattr(record, "name_servers", None)
    if isinstance(ns, list):
        data.nameservers = sorted({str(n).lower() for n in ns if n})
    elif ns:
        data.nameservers = [str(ns).lower()]

    return data
