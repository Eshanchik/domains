"""Daily digest: composition by channel scope + idempotency (DB + Redis)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.db import SessionLocal, get_redis
from app.models.alert import AlertEvent
from app.models.notification import NotificationChannel
from app.services import notifications as notif
from app.services.digest import compose_digest, run_digests

KYIV = ZoneInfo("Europe/Kyiv")


def _run(coro):
    return asyncio.run(coro)


def _add_event(domain_id: int, kind="expiry", days=5, key=None) -> None:
    async def _a():
        async with SessionLocal() as s:
            s.add(
                AlertEvent(
                    domain_id=domain_id,
                    kind=kind,
                    dedupe_key=key or f"{domain_id}:{kind}",
                    severity="medium",
                    state="active",
                    fired_at=datetime.now(UTC),
                    payload_json={"days": days},
                )
            )
            await s.commit()

    _run(_a())


def _make_channel(**kw) -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            ch = await notif.create_channel(
                s,
                name=kw.get("name", "ch"),
                chat_id="-100",
                company_id=kw.get("company_id"),
                project_id=kw.get("project_id"),
                is_default=kw.get("is_default", False),
                mode=kw.get("mode", "digest"),
                digest_time=kw.get("digest_time", "09:00"),
                actor_id=None,
            )
            return ch.id

    return _run(_c())


def test_compose_scoped_to_project(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    p2 = make_project(acme, code="shop")
    d1 = make_domain(p1, fqdn="in-scope.com")
    d2 = make_domain(p2, fqdn="out-scope.com")
    _add_event(d1)
    _add_event(d2)
    ch = _make_channel(project_id=p1)

    async def compose():
        async with SessionLocal() as s:
            channel = await s.get(NotificationChannel, ch)
            return await compose_digest(s, channel)

    text = _run(compose())
    assert "in-scope.com" in text
    assert "out-scope.com" not in text
    assert "Истекают домены" in text


def test_compose_empty_returns_none(make_company, make_project):
    acme = make_company(code="acme")
    make_project(acme, code="web")
    ch = _make_channel(is_default=True)

    async def compose():
        async with SessionLocal() as s:
            return await compose_digest(s, await s.get(NotificationChannel, ch))

    assert _run(compose()) is None  # no active events


def test_run_digests_idempotent_per_day(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    d1 = make_domain(p1, fqdn="example.com")
    _add_event(d1)
    ch = _make_channel(is_default=True, digest_time="09:00", mode="both")
    now = datetime(2026, 7, 20, 9, 0, tzinfo=KYIV)

    sent: list[int] = []

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                first = await run_digests(
                    s, redis, now_kyiv=now, send=lambda cid, t: sent.append(cid)
                )
            async with SessionLocal() as s:
                second = await run_digests(
                    s, redis, now_kyiv=now, send=lambda cid, t: sent.append(cid)
                )
            return first, second
        finally:
            await redis.aclose()

    first, second = _run(run())
    assert first == [ch]
    assert second == []  # already sent today
    assert sent == [ch]


def test_run_digests_only_at_matching_time(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    p1 = make_project(acme, code="web")
    d1 = make_domain(p1, fqdn="example.com")
    _add_event(d1)
    _make_channel(is_default=True, digest_time="10:00", mode="digest")
    now = datetime(2026, 7, 20, 9, 0, tzinfo=KYIV)  # 09:00, channel wants 10:00

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                return await run_digests(s, redis, now_kyiv=now, send=lambda cid, t: None)
        finally:
            await redis.aclose()

    assert _run(run()) == []  # not this minute
