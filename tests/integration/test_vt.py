"""VirusTotal check: config, detection, 429→stale, per-minute budget (DB+Redis+respx)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import respx
from sqlalchemy import select

from app.checks.vt import run_vt_check
from app.db import SessionLocal, get_redis
from app.models.check_result import CheckResult
from app.models.vt_result import VtResult
from app.services import settings_store

VT_RE = r"https://www\.virustotal\.com/api/v3/domains/.*"


def _run(coro):
    return asyncio.run(coro)


def _set_key(key: str = "test-vt-key") -> None:
    async def _s():
        async with SessionLocal() as s:
            await settings_store.set_secret(s, settings_store.VT_API_KEY, key)

    _run(_s())


def _ok_payload(malicious=0, suspicious=0):
    return {
        "data": {
            "attributes": {
                "last_analysis_stats": {
                    "harmless": 80,
                    "malicious": malicious,
                    "suspicious": suspicious,
                    "undetected": 3,
                },
                "reputation": -5 if malicious else 0,
                "categories": {"engine": "malware"} if malicious else {},
                "last_analysis_date": 1700000000,
            }
        }
    }


def test_not_configured_when_no_key(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                return await run_vt_check(s, redis, dom)
        finally:
            await redis.aclose()

    assert _run(run()) == "not_configured"


def test_clean_domain_is_ok(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _set_key()

    router = respx.mock(assert_all_called=False)
    router.get(url__regex=VT_RE).respond(json=_ok_payload())

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    status = await run_vt_check(
                        s, redis, dom, now=datetime(2026, 7, 20, 12, tzinfo=UTC)
                    )
            async with SessionLocal() as s:
                vt = (await s.execute(select(VtResult))).scalars().all()
                return status, len(list(vt))
        finally:
            await redis.aclose()

    status, n = _run(run())
    assert status == "ok"
    assert n == 1


def test_malicious_detection_is_fail(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="bad.com")
    _set_key()

    router = respx.mock(assert_all_called=False)
    router.get(url__regex=VT_RE).respond(json=_ok_payload(malicious=3))

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    status = await run_vt_check(
                        s, redis, dom, now=datetime(2026, 7, 20, 12, tzinfo=UTC)
                    )
            async with SessionLocal() as s:
                vt = (await s.execute(select(VtResult))).scalar_one()
                cr = (
                    await s.execute(select(CheckResult).where(CheckResult.type == "vt"))
                ).scalar_one()
                return status, vt.malicious, cr.status
        finally:
            await redis.aclose()

    status, malicious, cr_status = _run(run())
    assert status == "fail"
    assert malicious == 3
    assert cr_status == "fail"


def test_429_marks_stale(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _set_key()

    router = respx.mock(assert_all_called=False)
    router.get(url__regex=VT_RE).respond(429)

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    status = await run_vt_check(
                        s, redis, dom, now=datetime(2026, 7, 20, 12, tzinfo=UTC)
                    )
            async with SessionLocal() as s:
                stale = (
                    (await s.execute(select(CheckResult).where(CheckResult.status == "stale")))
                    .scalars()
                    .all()
                )
                return status, len(list(stale))
        finally:
            await redis.aclose()

    status, n_stale = _run(run())
    assert status == "stale"
    assert n_stale == 1


def test_per_minute_budget_limits_to_four(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    doms = [make_domain(proj, fqdn=f"d{i}.com") for i in range(5)]
    _set_key()

    router = respx.mock(assert_all_called=False)
    router.get(url__regex=VT_RE).respond(json=_ok_payload())
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)

    async def run():
        redis = get_redis()
        statuses = []
        try:
            with router:
                for d in doms:
                    async with SessionLocal() as s:
                        statuses.append(await run_vt_check(s, redis, d, now=now))
            return statuses
        finally:
            await redis.aclose()

    statuses = _run(run())
    assert statuses.count("ok") == 4  # 4/min budget
    assert statuses.count("rate_limited") == 1
