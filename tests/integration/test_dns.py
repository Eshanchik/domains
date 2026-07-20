"""DNS check + NS-change alerting (DB, mocked resolver)."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.checks import dns_check
from app.checks.dns_check import run_dns_check
from app.db import SessionLocal, get_redis
from app.models.alert import AlertEvent
from app.models.check_result import CheckResult
from app.models.domain import Domain
from app.services.alerts import evaluate_dns


def _run(coro):
    return asyncio.run(coro)


def _patch(monkeypatch, ns: list[str], a: list[str] | None = None) -> None:
    async def fake(fqdn, rdtype):
        if rdtype == "NS":
            return sorted(ns)
        if rdtype == "A":
            return sorted(a if a is not None else ["1.2.3.4"])
        return []

    monkeypatch.setattr(dns_check, "_resolve", fake)


def test_dns_check_writes_snapshot(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _patch(monkeypatch, ["a.ns.example", "b.ns.example"])

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                status = await run_dns_check(s, redis, dom)
            async with SessionLocal() as s:
                cr = (
                    await s.execute(select(CheckResult).where(CheckResult.type == "dns"))
                ).scalar_one()
                return status, cr.data_json["ns"], cr.data_json["a"]
        finally:
            await redis.aclose()

    status, ns, a = _run(run())
    assert status == "ok"
    assert ns == ["a.ns.example", "b.ns.example"]
    assert a == ["1.2.3.4"]


def test_ns_change_fires_high_alert(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")

    async def run():
        redis = get_redis()
        try:
            _patch(monkeypatch, ["a.ns.example", "b.ns.example"])
            async with SessionLocal() as s:
                await run_dns_check(s, redis, dom)
                first = await evaluate_dns(s, await s.get(Domain, dom))
                await s.commit()
            _patch(monkeypatch, ["evil.ns.attacker"])  # NS hijack
            async with SessionLocal() as s:
                await run_dns_check(s, redis, dom)
                second = await evaluate_dns(s, await s.get(Domain, dom))
                await s.commit()
            return len(first), second
        finally:
            await redis.aclose()

    n_first, second = _run(run())
    assert n_first == 0  # first snapshot has nothing to compare
    assert len(second) == 1
    assert second[0].kind == "ns_change"
    assert second[0].severity == "high"


def test_stable_ns_no_alert(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _patch(monkeypatch, ["a.ns.example"])

    async def run():
        redis = get_redis()
        try:
            for _ in range(2):
                async with SessionLocal() as s:
                    await run_dns_check(s, redis, dom)
                    ev = await evaluate_dns(s, await s.get(Domain, dom))
                    await s.commit()
            async with SessionLocal() as s:
                active = (
                    await s.execute(select(func.count()).select_from(AlertEvent))
                ).scalar_one()
            return len(ev), active
        finally:
            await redis.aclose()

    last_events, active = _run(run())
    assert last_events == 0
    assert active == 0  # no ns_change on stable NS


def test_unresolvable_is_stale(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="nx.invalid")

    async def fake(fqdn, rdtype):
        return []

    monkeypatch.setattr(dns_check, "_resolve", fake)

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                return await run_dns_check(s, redis, dom)
        finally:
            await redis.aclose()

    assert _run(run()) == "stale"
