"""Namecheap renewal pricing → domain cost: apply, manual-safe, cache, failures."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from app.connectors.base import ConnectorError, TldPrice
from app.connectors.namecheap import NamecheapConnector
from app.core.circuit_breaker import CircuitBreaker
from app.db import SessionLocal, get_redis
from app.models.domain import Domain
from app.models.registrar import RegistrarAccount
from app.services import pricing
from app.services import registrars as reg

PRICES = {
    "com": TldPrice(tld="com", price=Decimal("10.98"), currency="USD"),
    "io": TldPrice(tld="io", price=Decimal("34.98"), currency="USD"),
}


def _run(coro):
    return asyncio.run(coro)


class PricingConn(NamecheapConnector):
    """A NamecheapConnector whose network fetch is replaced by a canned result."""

    def __init__(self, prices=None, error=None, counter=None):
        super().__init__(api_user="u", api_key="k", username="u", client_ip="1.2.3.4")
        self._prices = prices if prices is not None else PRICES
        self._err = error
        self._counter = counter if counter is not None else []

    async def get_renewal_prices(self):
        self._counter.append(1)
        if self._err:
            raise ConnectorError(self._err)
        return self._prices


def _make_account() -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            acc = await reg.create_namecheap_account(
                s,
                label="main",
                api_user="u",
                api_key="SECRETKEY",
                username="u",
                client_ip="1.2.3.4",
                actor_id=None,
            )
            return acc.id

    return _run(_c())


async def _reset_redis(account_id: int) -> None:
    redis = get_redis()
    try:
        await redis.delete(pricing._cache_key(account_id))
        await redis.delete("cb:namecheap:fails", "cb:namecheap:open_until")
        await redis.delete("rl:svc:namecheap")
    finally:
        await redis.aclose()


def test_pricing_applies_to_account_domains(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    aid = _make_account()
    d_com = make_domain(proj, fqdn="shop.com", registrar_account_id=aid)
    d_io = make_domain(proj, fqdn="app.io", registrar_account_id=aid)
    # A .net domain has no price entry → left untouched.
    d_net = make_domain(proj, fqdn="other.net", registrar_account_id=aid)
    _run(_reset_redis(aid))

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            redis = get_redis()
            try:
                return await pricing.refresh_account_pricing(
                    s, acc, redis=redis, connector=PricingConn()
                )
            finally:
                await redis.aclose()

    report = _run(run())
    assert report.priced == 2 and report.tlds == 2 and report.error is None

    async def read():
        async with SessionLocal() as s:
            return {
                d.fqdn: (d.renewal_price, d.renewal_currency, (d.field_sources or {}))
                for d in (await s.execute(_domains_of([d_com, d_io, d_net]))).scalars().all()
            }

    rows = _run(read())
    assert rows["shop.com"][0] == Decimal("10.98") and rows["shop.com"][1] == "USD"
    assert rows["shop.com"][2].get("renewal_price") == "api-namecheap"
    assert rows["app.io"][0] == Decimal("34.98")
    assert rows["other.net"][0] is None  # no TLD price → unchanged


def _domains_of(ids):
    from sqlalchemy import select

    return select(Domain).where(Domain.id.in_(ids))


def test_pricing_does_not_overwrite_manual(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    aid = _make_account()
    make_domain(
        proj,
        fqdn="manual.com",
        registrar_account_id=aid,
        renewal_price=Decimal("99.00"),
        renewal_currency="EUR",
        field_sources={"fqdn": "manual", "renewal_price": "manual"},
    )
    _run(_reset_redis(aid))

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            redis = get_redis()
            try:
                report = await pricing.refresh_account_pricing(
                    s, acc, redis=redis, connector=PricingConn()
                )
            finally:
                await redis.aclose()
        async with SessionLocal() as s:
            from sqlalchemy import select

            d = (await s.execute(select(Domain).where(Domain.fqdn == "manual.com"))).scalar_one()
            return report, d.renewal_price, d.renewal_currency

    report, price, currency = _run(run())
    assert report.priced == 0
    assert price == Decimal("99.00") and currency == "EUR"  # manual preserved


def test_pricing_cache_avoids_second_api_call(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    aid = _make_account()
    make_domain(proj, fqdn="cache.com", registrar_account_id=aid)
    _run(_reset_redis(aid))
    counter: list[int] = []

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            redis = get_redis()
            try:
                conn = PricingConn(counter=counter)
                r1 = await pricing.refresh_account_pricing(s, acc, redis=redis, connector=conn)
                r2 = await pricing.refresh_account_pricing(s, acc, redis=redis, connector=conn)
                return r1, r2
            finally:
                await redis.aclose()

    r1, r2 = _run(run())
    assert len(counter) == 1  # API hit once; second run served from Redis cache
    assert r1.from_cache is False and r2.from_cache is True


def test_pricing_api_error_reports_and_no_crash(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    aid = _make_account()
    make_domain(proj, fqdn="err.com", registrar_account_id=aid)
    _run(_reset_redis(aid))

    async def run():
        async with SessionLocal() as s:
            acc = await s.get(RegistrarAccount, aid)
            redis = get_redis()
            try:
                return await pricing.refresh_account_pricing(
                    s, acc, redis=redis, connector=PricingConn(error="503 Service Unavailable")
                )
            finally:
                await redis.aclose()

    report = _run(run())
    assert report.priced == 0 and report.tlds == 0
    assert report.error and "503" in report.error


def test_pricing_circuit_open_skips_fetch(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    aid = _make_account()
    make_domain(proj, fqdn="cb.com", registrar_account_id=aid)
    _run(_reset_redis(aid))
    counter: list[int] = []

    now = datetime(2026, 7, 20, 12, tzinfo=UTC)

    async def run():
        redis = get_redis()
        try:
            # Force the breaker open at the same instant the fetch will check it.
            breaker = CircuitBreaker(redis, "namecheap")
            for _ in range(breaker.threshold):
                await breaker.record_failure(now=now.timestamp())
            async with SessionLocal() as s:
                acc = await s.get(RegistrarAccount, aid)
                return await pricing.refresh_account_pricing(
                    s, acc, redis=redis, connector=PricingConn(counter=counter), now=now
                )
        finally:
            await redis.aclose()

    report = _run(run())
    assert report.error == "circuit_open"
    assert len(counter) == 0  # never called the API while open
