"""Registrar renewal pricing (Namecheap) → domain renewal cost (SPEC FR-RG-6).

Pulls the 1-year RENEW price per TLD from ``namecheap.users.getPricing`` and writes
it onto the account's domains (``renewal_price`` / ``renewal_currency``). TLD prices
change rarely, so the fetched map is cached in Redis and only refreshed when cold —
the periodic sync then just applies the cached map to domains (cheap DB update).

External calls go through the centralized token bucket + circuit breaker + retry
(CLAUDE.md). Any failure marks the map stale and is surfaced in the report; it never
crashes the worker and never overwrites a manually-entered price (manual wins).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ConnectorError, TldPrice
from app.connectors.namecheap import NamecheapConnector
from app.core import rate_limiter
from app.core.circuit_breaker import CircuitBreaker
from app.core.retry import RetryError, with_retry
from app.models.domain import Domain
from app.models.registrar import RegistrarAccount
from app.services import registrars as reg

log = logging.getLogger("pricing")

PRICE_SOURCE = "api-namecheap"
CACHE_TTL_SECONDS = 24 * 3600
# Namecheap allows generous rates; keep pricing well under to be a good citizen.
PER_MIN_CAPACITY = 6.0
PER_MIN_REFILL = 6.0 / 60.0


@dataclass
class PricingReport:
    priced: int = 0
    tlds: int = 0
    from_cache: bool = False
    error: str | None = None


def _cache_key(account_id: int) -> str:
    return f"dg:pricing:namecheap:{account_id}"


def _encode(prices: dict[str, TldPrice]) -> str:
    return json.dumps({tld: [str(p.price), p.currency] for tld, p in prices.items()})


def _decode(raw: str) -> dict[str, TldPrice]:
    data = json.loads(raw)
    return {
        tld: TldPrice(tld=tld, price=Decimal(price), currency=cur)
        for tld, (price, cur) in data.items()
    }


async def get_pricing_map(
    session: AsyncSession,
    account: RegistrarAccount,
    *,
    redis: aioredis.Redis,
    connector: NamecheapConnector | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[dict[str, TldPrice], bool, str | None]:
    """Return ``(prices, from_cache, error)`` — cached map or a fresh API fetch.

    A non-empty cache is reused unless ``force``. On a cold cache the API is called
    through the token bucket + circuit breaker + retry; failures return ``({}, ...,
    error)`` rather than raising.
    """
    ts = now or datetime.now(UTC)
    if not force:
        cached = await redis.get(_cache_key(account.id))
        if cached:
            return _decode(cached), True, None

    allowed, _retry = await rate_limiter.acquire_token(
        redis,
        rate_limiter.service_key("namecheap"),
        capacity=PER_MIN_CAPACITY,
        refill_rate=PER_MIN_REFILL,
        now=ts.timestamp(),
    )
    if not allowed:
        return {}, False, "rate_limited"

    breaker = CircuitBreaker(redis, "namecheap")
    if not await breaker.allow(now=ts.timestamp()):
        return {}, False, "circuit_open"

    conn = connector or await reg.build_account_connector(session, account)
    if not isinstance(conn, NamecheapConnector):
        return {}, False, "unsupported"

    try:
        prices = await with_retry(conn.get_renewal_prices, retries=3, exceptions=(ConnectorError,))
        await breaker.record_success()
    except (RetryError, ConnectorError) as exc:
        await breaker.record_failure(now=ts.timestamp())
        inner = exc.last_exc if isinstance(exc, RetryError) else exc
        return {}, False, str(inner)

    await redis.set(_cache_key(account.id), _encode(prices), ex=CACHE_TTL_SECONDS)
    return prices, False, None


async def refresh_account_pricing(
    session: AsyncSession,
    account: RegistrarAccount,
    *,
    redis: aioredis.Redis,
    connector: NamecheapConnector | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> PricingReport:
    """Fetch (or reuse) the TLD price map and apply it to the account's domains."""
    prices, from_cache, error = await get_pricing_map(
        session, account, redis=redis, connector=connector, now=now, force=force
    )
    report = PricingReport(tlds=len(prices), from_cache=from_cache, error=error)
    if not prices:
        return report

    domains = (
        (
            await session.execute(
                select(Domain).where(
                    Domain.registrar_account_id == account.id, Domain.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    for domain in domains:
        priced = _apply_price(domain, prices.get(domain.tld))
        if priced:
            report.priced += 1
    await session.commit()
    return report


def _apply_price(domain: Domain, price: TldPrice | None) -> bool:
    """Set the domain's renewal price from ``price`` unless it was set manually."""
    if price is None:
        return False
    field_sources = dict(domain.field_sources or {})
    if field_sources.get("renewal_price") == "manual":
        return False  # manual wins over autosync (FR-RG-5)
    if domain.renewal_price == price.price and domain.renewal_currency == price.currency:
        return False  # idempotent — no change, no history row
    domain.renewal_price = price.price
    domain.renewal_currency = price.currency
    field_sources["renewal_price"] = PRICE_SOURCE
    field_sources["renewal_currency"] = PRICE_SOURCE
    domain.field_sources = field_sources
    domain.updated_at = datetime.now(UTC)
    return True
