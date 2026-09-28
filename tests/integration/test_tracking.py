"""T99: tracking board (status, since, uptime, history, flags) and management."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.checks.check_result_store import write_result
from app.db import SessionLocal
from app.models.domain import Domain
from app.models.healthcheck import HealthCheck, HealthCheckResult
from app.models.vt_result import VtResult
from app.services import companies as companies_svc
from app.services import tracking as t

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


def _tag(domain_id: int, name: str = "tracking") -> None:
    async def _c():
        async with SessionLocal() as s:
            d = await s.get(Domain, domain_id)
            tag = await companies_svc.get_or_create_tag(s, name)
            d.tags = [*d.tags, tag]
            await s.commit()

    _run(_c())


def _check(domain_id: int, *, state="up", failures=0, results=(), url=None, enabled=True) -> int:
    """A health-check plus results given as (minutes_ago, ok, status_code, error)."""

    async def _c() -> int:
        async with SessionLocal() as s:
            d = await s.get(Domain, domain_id)
            hc = HealthCheck(
                domain_id=domain_id,
                url=url or f"https://{d.fqdn}/click?pid=1&offer_id=625",
                expected_statuses="200-399",
                state=state,
                consecutive_failures=failures,
                last_checked_at=NOW if results else None,
                is_enabled=enabled,
            )
            s.add(hc)
            await s.flush()
            for minutes_ago, ok, code, err in results:
                s.add(
                    HealthCheckResult(
                        healthcheck_id=hc.id,
                        ok=ok,
                        status_code=code,
                        latency_ms=120 if ok else None,
                        error=err,
                        checked_at=NOW - timedelta(minutes=minutes_ago),
                    )
                )
            await s.commit()
            return hc.id

    return _run(_c())


def _board(**kw) -> t.TrackingBoard:
    async def _c():
        async with SessionLocal() as s:
            return await t.build_board(s, allowed=kw.pop("allowed", None), now=NOW, **kw)

    return _run(_c())


def _setup(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    return acme, proj, make_domain


def test_board_status_since_uptime_history(make_company, make_project, make_domain):
    acme, proj, _ = _setup(make_company, make_project, make_domain)
    down = make_domain(proj, fqdn="down.com")
    up = make_domain(proj, fqdn="up.com")
    flaky = make_domain(proj, fqdn="flaky.com")
    bare = make_domain(proj, fqdn="bare.com")
    untagged = make_domain(proj, fqdn="notracking.com")
    archived = make_domain(proj, fqdn="archived.com", is_active=False)
    for d in (down, up, flaky, bare, archived):
        _tag(d)
    # down: last OK 3 days ago, failing every 15 min since (only the last day stored).
    _check(
        down,
        state="down",
        failures=288,
        results=[(3 * 24 * 60, True, 302, None)]
        + [(m, False, None, "request failed: ") for m in range(0, 24 * 60, 15)],
    )
    # up: all good for the last day, one failure 2 days ago.
    _check(
        up,
        results=[(2 * 24 * 60, False, 500, "unexpected status 500")]
        + [(m, True, 302, None) for m in range(0, 24 * 60, 15)],
    )
    _check(flaky, state="up", failures=1, results=[(15, True, 302, None), (0, False, None, "x")])
    _check(untagged, results=[(0, True, 302, None)])

    board = _board()
    fqdns = [r.fqdn for r in board.rows]
    assert "notracking.com" not in fqdns and "archived.com" not in fqdns
    assert fqdns[:3] == ["down.com", "flaky.com", "bare.com"]  # down → degraded → no check → up
    rows = {r.fqdn: r for r in board.rows}

    d = rows["down.com"]
    assert d.status == "down" and d.since_label == "лежит 3 д 0 ч"
    assert d.uptime_24h == 0.0
    assert len(d.spark) == t.SPARK_LEN and not d.spark[-1].ok
    assert d.last_reason.startswith("нет соединения")  # blank error made understandable

    u = rows["up.com"]
    assert u.status == "up" and u.since_label == "работает 2 д 0 ч"
    assert u.uptime_24h == 100.0 and u.last_code == 302 and u.last_latency_ms == 120
    assert u.uptime_7d is not None and u.uptime_7d < 100.0  # the failure 2 days ago counts

    assert rows["flaky.com"].status == "degraded"
    assert rows["bare.com"].status == "nocheck"
    assert board.totals == {
        "down": 1,
        "degraded": 1,
        "stale": 0,
        "unknown": 0,
        "nocheck": 1,
        "up": 1,
        "total": 4,
        "vt": 0,
        "expiring": 0,
    }


def test_board_flags_scope_and_filters(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    other = make_company(code="other")
    pa = make_project(acme, code="web")
    po = make_project(other, code="web")
    a = make_domain(pa, fqdn="a-track.com", expiry_date=NOW + timedelta(days=10))
    o = make_domain(po, fqdn="o-track.com")
    _tag(a)
    _tag(o, name="Tracking")  # the tag name is case-insensitive
    _check(a, results=[(0, True, 302, None)])

    async def flags():
        async with SessionLocal() as s:
            s.add(VtResult(domain_id=a, malicious=3, harmless=80, checked_at=NOW))
            await write_result(s, domain_id=a, check_type="dns", status="ok", checked_at=NOW)
            await s.commit()

    _run(flags())
    board = _board()
    row = next(r for r in board.rows if r.fqdn == "a-track.com")
    assert row.vt_malicious == 3 and row.dns_status == "ok" and row.expiry_days == 10
    assert board.totals["vt"] == 1 and board.totals["expiring"] == 1
    assert {r.fqdn for r in board.rows} == {"a-track.com", "o-track.com"}

    assert [r.fqdn for r in _board(allowed={pa}).rows] == ["a-track.com"]
    assert _board(allowed=set()).rows == []
    assert [r.fqdn for r in _board(company_id=other).rows] == ["o-track.com"]
    assert [r.fqdn for r in _board(status="nocheck").rows] == ["o-track.com"]
    assert [r.fqdn for r in _board(q="A-TR").rows] == ["a-track.com"]


# --- management -----------------------------------------------------------------------


def _add(fqdns, allowed=None, template="https://{fqdn}/click?pid=1", **kw):
    async def _c():
        async with SessionLocal() as s:
            return await t.add_to_tracking(
                s,
                fqdns=fqdns,
                allowed=allowed,
                url_template=template,
                expected_statuses=kw.get("expected", "200-399"),
                follow_redirects=kw.get("follow", False),
                actor_id=None,
            )

    return _run(_c())


def _checks(domain_id: int) -> list[HealthCheck]:
    async def _c():
        async with SessionLocal() as s:
            rows = await s.execute(select(HealthCheck).where(HealthCheck.domain_id == domain_id))
            return list(rows.scalars().all())

    return _run(_c())


def test_add_to_tracking_is_idempotent_and_reports(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    other = make_company(code="other")
    pa = make_project(acme, code="web")
    po = make_project(other, code="web")
    a = make_domain(pa, fqdn="new-track.com")
    make_domain(po, fqdn="foreign.com")

    rep = _add(["New-Track.com", "missing.com", "foreign.com", "not a domain", ""], allowed={pa})
    assert rep.added == ["new-track.com"] and rep.checks_created == 1
    assert rep.missing == ["missing.com"] and rep.forbidden == ["foreign.com"]
    assert rep.invalid == ["not a domain"]
    [hc] = _checks(a)
    assert hc.url == "https://new-track.com/click?pid=1" and hc.expected_statuses == "200-399"

    again = _add(["new-track.com"], allowed={pa})
    assert again.added == [] and again.already == ["new-track.com"] and again.checks_created == 0
    assert len(_checks(a)) == 1
    assert [r.fqdn for r in _board().rows] == ["new-track.com"]


def test_add_to_tracking_rejects_bad_templates(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    pa = make_project(acme, code="web")
    a = make_domain(pa, fqdn="x.com")
    with pytest.raises(t.TrackingError, match=r"\{fqdn\}"):
        _add(["x.com"], template="https://static.example/click")
    with pytest.raises(t.TrackingError, match="Недопустимый URL"):
        _add(["x.com"], template="ftp://{fqdn}/click")
    assert _checks(a) == [] and _board().rows == []  # nothing half-applied


def test_suggest_remove_and_recheck(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    pa = make_project(acme, code="web")
    a = make_domain(pa, fqdn="s.com")
    b = make_domain(pa, fqdn="t.com")
    _add(["s.com", "t.com"], template="https://{fqdn}/click?pid=1&offer_id=625")

    async def suggest():
        async with SessionLocal() as s:
            return await t.suggest_template(s)

    assert _run(suggest()) == "https://{fqdn}/click?pid=1&offer_id=625"

    async def recheck(domain_id, allowed=None):
        async with SessionLocal() as s:
            return await t.recheck(s, domain_id, allowed=allowed, actor_id=None, now=NOW)

    assert _run(recheck(a)) == 1
    assert _checks(a)[0].next_check_at == NOW
    assert _run(recheck(a, allowed=set())) == 0  # out of scope

    async def remove(domain_id):
        async with SessionLocal() as s:
            return await t.remove_from_tracking(s, domain_id, allowed=None, actor_id=None)

    assert _run(remove(b)) is True
    assert _run(remove(b)) is False
    assert [r.fqdn for r in _board().rows] == ["s.com"]
    assert len(_checks(b)) == 1  # the health-check stays on the domain


def test_access_rules_roundtrip_and_scope(make_company, make_project):
    adera = make_company(code="adera", name="Adera")
    p1 = make_project(adera, code="ad")
    other = make_company(code="gt1")
    make_project(other, code="kgb")

    async def flow():
        async with SessionLocal() as s:
            await t.add_access_rule(
                s, email_domain="@Adera.Agency", company_id=adera, actor_id=None
            )
            await t.add_access_rule(s, email_domain="adera.agency", company_id=adera, actor_id=None)
            rules = await t.get_access_rules(s)
            scope = await t.scope_for_rule(s, rules[0])
            await t.delete_access_rule(s, email_domain="adera.agency", actor_id=None)
            return rules, scope, await t.get_access_rules(s)

    rules, scope, after = _run(flow())
    assert rules == [t.AccessRule("adera.agency", adera)]  # re-adding replaces
    assert scope == {p1}
    assert after == []


# --- review fixes ----------------------------------------------------------------------


def test_stale_check_is_not_reported_as_up(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    d = make_domain(proj, fqdn="silent.com")
    _tag(d)
    hc = _check(d, results=[(180, True, 302, None)])

    async def last_checked_3h_ago():
        async with SessionLocal() as s:
            h = await s.get(HealthCheck, hc)
            h.last_checked_at = NOW - timedelta(hours=3)  # interval 15 min → stale after 1 h
            await s.commit()

    _run(last_checked_3h_ago())
    [row] = _board().rows
    assert row.status == "stale" and row.since_label == "последняя — 3 ч 0 мин назад"


def test_totals_ignore_the_status_filter(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    for name, ok in (("a.com", True), ("b.com", False)):
        d = make_domain(proj, fqdn=name)
        _tag(d)
        _check(d, state="up" if ok else "down", results=[(0, ok, 302 if ok else None, None)])
    board = _board(status="down")
    assert [r.fqdn for r in board.rows] == ["b.com"]
    assert board.totals["total"] == 2 and board.totals["up"] == 1 and board.totals["down"] == 1


def test_add_reuses_differently_cased_tag_and_reports_archived(
    make_company, make_project, make_domain
):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    tagged = make_domain(proj, fqdn="cased.com")
    make_domain(proj, fqdn="old.com", is_active=False)
    _tag(tagged, name="Tracking")
    rep = _add(["cased.com", "old.com"])
    assert rep.already == ["cased.com"] and rep.added == []
    assert rep.archived == ["old.com"] and rep.missing == []

    async def tag_names():
        async with SessionLocal() as s:
            d = await s.get(Domain, tagged)
            return sorted(t.name for t in d.tags)

    assert _run(tag_names()) == ["Tracking"]  # no twin "tracking" tag added
