"""Health-check execution + state machine (DB + respx)."""

from __future__ import annotations

import asyncio

import httpx
import respx

from app.checks.healthcheck import run_healthcheck
from app.db import SessionLocal, get_redis
from app.models.healthcheck import HealthCheck


def _run(coro):
    return asyncio.run(coro)


def _make_hc(domain_id: int, **kw) -> int:
    async def create() -> int:
        async with SessionLocal() as s:
            hc = HealthCheck(
                domain_id=domain_id,
                url=kw.get("url", "https://example.com/click?pid=1&offer_id=625"),
                method=kw.get("method", "GET"),
                follow_redirects=kw.get("follow_redirects", False),
                expected_statuses=kw.get("expected_statuses", "301,302"),
                location_pattern=kw.get("location_pattern"),
                body_substring=kw.get("body_substring"),
                fail_threshold=kw.get("fail_threshold", 3),
            )
            s.add(hc)
            await s.commit()
            await s.refresh(hc)
            return hc.id

    return _run(create())


def _check_once(hc_id: int) -> tuple[str, str | None, int, bool]:
    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                out = await run_healthcheck(s, redis, hc_id)
            return out.state, out.transition, out.consecutive_failures, out.ok
        finally:
            await redis.aclose()

    return _run(run())


def test_redirect_with_location_pattern_is_up(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="forgeofreason.com")
    hc = _make_hc(
        dom,
        url="https://forgeofreason.com/click?pid=1&offer_id=625",
        expected_statuses="301,302",
        location_pattern="offer",
    )

    router = respx.mock(assert_all_called=False)
    router.get(url__regex=r".*/click.*").mock(
        return_value=httpx.Response(302, headers={"Location": "https://offer.example/landing"})
    )

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    out = await run_healthcheck(s, redis, hc)
            return out.state, out.ok
        finally:
            await redis.aclose()

    state, ok = _run(run())
    assert ok is True
    assert state == "up"


def test_flapping_below_threshold_does_not_go_down(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    hc = _make_hc(dom, expected_statuses="200", fail_threshold=3)

    router = respx.mock(assert_all_called=False)
    router.get(url__regex=r".*").mock(return_value=httpx.Response(500))

    async def run():
        redis = get_redis()
        transitions = []
        states = []
        try:
            with router:
                for _ in range(2):  # two failures, threshold is 3
                    async with SessionLocal() as s:
                        out = await run_healthcheck(s, redis, hc)
                        transitions.append(out.transition)
                        states.append(out.state)
            return transitions, states
        finally:
            await redis.aclose()

    transitions, states = _run(run())
    assert transitions == [None, None]  # no down alert yet
    assert "down" not in states


def test_down_after_threshold_then_recovered(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    hc = _make_hc(dom, expected_statuses="200", fail_threshold=3)

    fail_router = respx.mock(assert_all_called=False)
    fail_router.get(url__regex=r".*").mock(return_value=httpx.Response(500))
    ok_router = respx.mock(assert_all_called=False)
    ok_router.get(url__regex=r".*").mock(return_value=httpx.Response(200))

    async def run():
        redis = get_redis()
        try:
            down_transition = None
            with fail_router:
                for _ in range(3):  # third failure crosses the threshold
                    async with SessionLocal() as s:
                        out = await run_healthcheck(s, redis, hc)
                        if out.transition:
                            down_transition = out.transition
            with ok_router:
                async with SessionLocal() as s:
                    recovered = await run_healthcheck(s, redis, hc)
            return down_transition, recovered.transition, recovered.state
        finally:
            await redis.aclose()

    down_transition, recovered_transition, final_state = _run(run())
    assert down_transition == "down"
    assert recovered_transition == "recovered"
    assert final_state == "up"


def test_bulk_template_substitutes_fqdn(make_company, make_project, make_domain):
    from app.schemas.healthcheck import HealthCheckCreate
    from app.services import healthchecks as svc

    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d1 = make_domain(proj, fqdn="one.com")
    d2 = make_domain(proj, fqdn="two.com")

    async def run():
        async with SessionLocal() as s:
            tpl = HealthCheckCreate(url="https://{fqdn}/health", expected_statuses="200")
            n = await svc.bulk_add_template(s, [d1, d2], tpl, actor_id=None)
        async with SessionLocal() as s:
            urls = {h.url for h in await svc.list_for_domain(s, d1)} | {
                h.url for h in await svc.list_for_domain(s, d2)
            }
            return n, urls

    n, urls = _run(run())
    assert n == 2
    assert urls == {"https://one.com/health", "https://two.com/health"}
