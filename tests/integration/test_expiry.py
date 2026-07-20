"""Expiry check: RDAP success, WHOIS fallback, stale on failure (DB + Redis + respx)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import respx
from sqlalchemy import func, select

from app.checks import whois as whois_mod
from app.checks.expiry import run_expiry_check
from app.checks.rdap import IANA_BOOTSTRAP_URL
from app.db import SessionLocal, get_redis
from app.models.check_result import CheckResult
from app.models.domain import Domain, DomainFieldHistory

BOOTSTRAP = {"services": [[["com"], ["https://rdap.example/"]]]}
RDAP_PAYLOAD = {
    "events": [{"eventAction": "expiration", "eventDate": "2027-05-01T00:00:00Z"}],
    "status": ["client transfer prohibited"],
    "nameservers": [{"ldhName": "ns1.example.com"}],
    "entities": [],
}


def _run(coro):
    return asyncio.run(coro)


def test_rdap_success_updates_domain_and_writes_result(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")

    router = respx.mock(assert_all_called=False)
    router.get(IANA_BOOTSTRAP_URL).respond(json=BOOTSTRAP)
    router.get("https://rdap.example/domain/example.com").respond(json=RDAP_PAYLOAD)

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    status = await run_expiry_check(s, redis, dom)
            async with SessionLocal() as s:
                d = await s.get(Domain, dom)
                results = (
                    (await s.execute(select(CheckResult).where(CheckResult.domain_id == dom)))
                    .scalars()
                    .all()
                )
                hist = (
                    await s.execute(
                        select(func.count())
                        .select_from(DomainFieldHistory)
                        .where(DomainFieldHistory.field == "expiry_date")
                    )
                ).scalar_one()
                return (
                    status,
                    d.expiry_date,
                    d.nameservers,
                    d.field_sources,
                    len(list(results)),
                    hist,
                )
        finally:
            await redis.aclose()

    status, expiry, ns, sources, n_results, hist = _run(run())
    assert status == "ok"
    assert expiry.year == 2027
    assert ns == ["ns1.example.com"]
    assert sources["expiry_date"] == "rdap"
    assert n_results == 1
    assert hist == 1


def test_rdap_5xx_falls_back_to_whois(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")

    monkeypatch.setattr(
        whois_mod,
        "_whois_lookup",
        lambda fqdn: SimpleNamespace(
            expiration_date=datetime(2029, 3, 1),
            creation_date=None,
            status=None,
            name_servers=["ns9.example.com"],
        ),
    )
    router = respx.mock(assert_all_called=False)
    router.get(IANA_BOOTSTRAP_URL).respond(json=BOOTSTRAP)
    router.get("https://rdap.example/domain/example.com").respond(503)

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    status = await run_expiry_check(s, redis, dom)
            async with SessionLocal() as s:
                d = await s.get(Domain, dom)
                return status, d.expiry_date, d.field_sources.get("expiry_date")
        finally:
            await redis.aclose()

    status, expiry, source = _run(run())
    assert status == "ok"
    assert expiry.year == 2029
    assert source == "whois"


def test_rdap_and_whois_fail_marks_stale_without_wiping(
    make_company, make_project, make_domain, monkeypatch
):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    # Domain already has a (manually set) expiry that must survive a failed check.
    existing = datetime(2026, 12, 1, tzinfo=UTC)
    dom = make_domain(proj, fqdn="example.com", expiry_date=existing)

    def _boom(fqdn):
        raise OSError("whois down")

    monkeypatch.setattr(whois_mod, "_whois_lookup", _boom)
    router = respx.mock(assert_all_called=False)
    router.get(IANA_BOOTSTRAP_URL).respond(json=BOOTSTRAP)
    router.get("https://rdap.example/domain/example.com").respond(503)

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    status = await run_expiry_check(s, redis, dom)
            async with SessionLocal() as s:
                d = await s.get(Domain, dom)
                stale = (
                    (await s.execute(select(CheckResult).where(CheckResult.status == "stale")))
                    .scalars()
                    .all()
                )
                return status, d.expiry_date, len(list(stale))
        finally:
            await redis.aclose()

    status, expiry, n_stale = _run(run())
    assert status == "stale"
    assert expiry == existing  # data preserved, not wiped
    assert n_stale == 1


def test_manual_expiry_not_overwritten(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    manual_expiry = datetime(2030, 1, 1, tzinfo=UTC)
    dom = make_domain(
        proj,
        fqdn="example.com",
        expiry_date=manual_expiry,
        field_sources={"fqdn": "manual", "expiry_date": "manual"},
    )

    router = respx.mock(assert_all_called=False)
    router.get(IANA_BOOTSTRAP_URL).respond(json=BOOTSTRAP)
    router.get("https://rdap.example/domain/example.com").respond(json=RDAP_PAYLOAD)

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    await run_expiry_check(s, redis, dom)
            async with SessionLocal() as s:
                d = await s.get(Domain, dom)
                return d.expiry_date, d.nameservers
        finally:
            await redis.aclose()

    expiry, ns = _run(run())
    assert expiry == manual_expiry  # manual value preserved
    assert ns == ["ns1.example.com"]  # non-manual field still updated
