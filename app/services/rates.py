"""Currency → USD exchange rates (SPEC FR-CT-3).

Fetches from an open rates API (exchangerate.host), cached per (currency, day) in
Redis. Callers may override the rate manually. On API failure the function returns
None so the caller can fall back to a manual rate instead of failing.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx
import redis.asyncio as aioredis

log = logging.getLogger("services.rates")

RATES_URL = "https://api.exchangerate.host/latest"
_CACHE_TTL = 24 * 3600


def _cache_key(currency: str, day: str) -> str:
    return f"rate:{currency.upper()}:{day}"


async def get_rate_to_usd(
    redis: aioredis.Redis,
    currency: str,
    *,
    day: str,
    client: httpx.AsyncClient | None = None,
) -> Decimal | None:
    """Return how many USD one unit of ``currency`` is worth, or None on failure.

    ``day`` (YYYYMMDD) scopes the cache. USD is always 1.
    """
    currency = currency.upper()
    if currency == "USD":
        return Decimal(1)

    cached = await redis.get(_cache_key(currency, day))
    if cached is not None:
        return Decimal(cached)

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.get(
            RATES_URL, params={"base": currency, "symbols": "USD"}, timeout=10.0
        )
        resp.raise_for_status()
        rate = resp.json().get("rates", {}).get("USD")
        if rate is None:
            return None
        value = Decimal(str(rate))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("rate fetch failed for %s: %s", currency, exc)
        return None
    finally:
        if owns:
            await client.aclose()

    await redis.set(_cache_key(currency, day), str(value), ex=_CACHE_TTL)
    return value
