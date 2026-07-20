"""Cost accounting: FX rates, payments, cost summary, forecast (DB + Redis + respx)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import respx

from app.db import SessionLocal, get_redis
from app.models.user import Role, User
from app.services import payments as svc
from app.services import rates as rates_svc

RATES_RE = r"https://api\.exchangerate\.host/latest.*"
NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


def _admin() -> User:
    return User(email="a@e.com", login="a", password_hash="x", role=Role.admin, is_active=True)


def test_rate_usd_is_one():
    async def run():
        redis = get_redis()
        try:
            return await rates_svc.get_rate_to_usd(redis, "USD", day="20260720")
        finally:
            await redis.aclose()

    assert _run(run()) == Decimal(1)


def test_rate_fetched_and_cached():
    router = respx.mock(assert_all_called=False)
    route = router.get(url__regex=RATES_RE).respond(json={"rates": {"USD": 1.08}})

    async def run():
        redis = get_redis()
        try:
            with router:
                r1 = await rates_svc.get_rate_to_usd(redis, "EUR", day="20260720")
                r2 = await rates_svc.get_rate_to_usd(redis, "EUR", day="20260720")  # cached
                return r1, r2, route.call_count  # read count before respx resets
        finally:
            await redis.aclose()

    r1, r2, calls = _run(run())
    assert r1 == Decimal("1.08")
    assert r2 == Decimal("1.08")
    assert calls == 1  # second lookup served from cache


def test_rate_api_failure_returns_none():
    router = respx.mock(assert_all_called=False)
    router.get(url__regex=RATES_RE).respond(503)

    async def run():
        redis = get_redis()
        try:
            with router:
                return await rates_svc.get_rate_to_usd(redis, "GBP", day="20260720")
        finally:
            await redis.aclose()

    assert _run(run()) is None


def _add(domain_id, **kw):
    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                return await svc.add_payment(
                    s, redis, domain_id=domain_id, actor_id=None, paid_at=NOW, **kw
                )
        finally:
            await redis.aclose()

    return _run(run())


def test_payment_usd(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    p = _add(dom, amount=Decimal("12.00"), currency="USD")
    assert p.rate_to_usd == Decimal(1)
    assert p.amount_usd == Decimal("12.00")


def test_payment_eur_uses_fetched_rate(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    router = respx.mock(assert_all_called=False)
    router.get(url__regex=RATES_RE).respond(json={"rates": {"USD": 1.10}})

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    return await svc.add_payment(
                        s,
                        redis,
                        domain_id=dom,
                        amount=Decimal("10.00"),
                        currency="EUR",
                        paid_at=NOW,
                        actor_id=None,
                    )
        finally:
            await redis.aclose()

    p = _run(run())
    assert p.rate_to_usd == Decimal("1.10")
    assert p.amount_usd == Decimal("11.00")


def test_payment_rate_unavailable_raises(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    router = respx.mock(assert_all_called=False)
    router.get(url__regex=RATES_RE).respond(503)

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    try:
                        await svc.add_payment(
                            s,
                            redis,
                            domain_id=dom,
                            amount=Decimal("10"),
                            currency="EUR",
                            paid_at=NOW,
                            actor_id=None,
                        )
                        return "ok"
                    except svc.RateUnavailableError:
                        return "rate_unavailable"
        finally:
            await redis.aclose()

    assert _run(run()) == "rate_unavailable"


def test_payment_rate_override(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    p = _add(dom, amount=Decimal("100"), currency="UAH", rate_override=Decimal("0.025"))
    assert p.rate_to_usd == Decimal("0.025")
    assert p.amount_usd == Decimal("2.50")


def test_cost_summary_by_company(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d1 = make_domain(proj, fqdn="a.com")
    d2 = make_domain(proj, fqdn="b.com")
    _add(d1, amount=Decimal("10"), currency="USD")
    _add(d2, amount=Decimal("15"), currency="USD")

    async def run():
        async with SessionLocal() as s:
            return await svc.cost_summary(
                s,
                _admin(),
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2027, 1, 1, tzinfo=UTC),
                group_by="company",
            )

    rows = _run(run())
    assert len(rows) == 1
    assert rows[0].label == "ACME"
    assert rows[0].total_usd == Decimal("25.00")


def test_upcoming_renewals(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    make_domain(
        proj, fqdn="soon.com", expiry_date=NOW + timedelta(days=10), renewal_price=Decimal("9.99")
    )
    make_domain(
        proj, fqdn="later.com", expiry_date=NOW + timedelta(days=200), renewal_price=Decimal("9.99")
    )
    make_domain(proj, fqdn="noprice.com", expiry_date=NOW + timedelta(days=5))

    async def run():
        async with SessionLocal() as s:
            return await svc.upcoming_renewals(s, _admin(), days=30, now=NOW)

    items = _run(run())
    assert [i.fqdn for i in items] == ["soon.com"]  # only one within 30d with a price
