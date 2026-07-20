"""Notification routing resolver + Telegram delivery (DB + respx)."""

from __future__ import annotations

import asyncio

import httpx
import respx
from sqlalchemy import func, select

from app.db import SessionLocal, get_redis
from app.models.notification import NotificationChannel, NotificationLog
from app.services import notifications as svc
from app.services import settings_store

TG_RE = r"https://api\.telegram\.org/bot.*/sendMessage"


def _run(coro):
    return asyncio.run(coro)


def _make_channel(**kw) -> int:
    async def create() -> int:
        async with SessionLocal() as s:
            cid = await svc.create_channel(
                s,
                name=kw.get("name", "ch"),
                chat_id=kw.get("chat_id", "-100123"),
                company_id=kw.get("company_id"),
                project_id=kw.get("project_id"),
                is_default=kw.get("is_default", False),
                mode=kw.get("mode", "both"),
                digest_time=None,
                actor_id=None,
            )
            return cid.id

    return _run(create())


def _set_bot_token(token="BOT:TOKEN"):
    async def _s():
        async with SessionLocal() as s:
            await settings_store.set_secret(s, settings_store.TELEGRAM_BOT_TOKEN, token)

    _run(_s())


def test_resolver_prefers_project_then_company_then_global(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _make_channel(name="glob", is_default=True)
    comp_ch = _make_channel(name="comp", company_id=acme)
    proj_ch = _make_channel(name="proj", project_id=proj)

    async def resolve():
        async with SessionLocal() as s:
            from app.models.domain import Domain

            d = await s.get(Domain, dom)
            names = [c.name for c in await svc.resolve_channels(s, d)]
            return names

    # Project level wins.
    assert _run(resolve()) == ["proj"]

    # Remove project channel → company level.
    _run(_delete(proj_ch))
    assert _run(resolve()) == ["comp"]

    # Remove company channel → global default.
    _run(_delete(comp_ch))
    assert _run(resolve()) == ["glob"]


async def _delete(channel_id: int):
    async with SessionLocal() as s:
        ch = await svc.get_channel(s, channel_id)
        await svc.delete_channel(s, ch, actor_id=None)


def test_resolver_mode_filter(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _make_channel(name="digest-only", project_id=proj, mode="digest")
    _make_channel(name="instant-only", project_id=proj, mode="instant")

    async def resolve(purpose):
        async with SessionLocal() as s:
            from app.models.domain import Domain

            d = await s.get(Domain, dom)
            return sorted(c.name for c in await svc.resolve_channels(s, d, purpose=purpose))

    assert _run(resolve("instant")) == ["instant-only"]
    assert _run(resolve("digest")) == ["digest-only"]


def test_send_success_logs_sent():
    ch = _make_channel(name="ch", chat_id="-100999")
    _set_bot_token()
    router = respx.mock(assert_all_called=False)
    router.post(url__regex=TG_RE).respond(json={"ok": True})

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    channel = await svc.get_channel(s, ch)
                    ok = await svc.send_to_channel(s, redis, channel, "hi")
            async with SessionLocal() as s:
                sent = (
                    await s.execute(
                        select(func.count())
                        .select_from(NotificationLog)
                        .where(NotificationLog.delivery_status == "sent")
                    )
                ).scalar_one()
            return ok, sent
        finally:
            await redis.aclose()

    ok, sent = _run(run())
    assert ok is True
    assert sent == 1


def test_send_retries_on_429_then_succeeds():
    ch = _make_channel(name="ch")
    _set_bot_token()
    router = respx.mock(assert_all_called=False)
    router.post(url__regex=TG_RE).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
    )

    async def run():
        redis = get_redis()
        try:
            with router:
                async with SessionLocal() as s:
                    channel = await svc.get_channel(s, ch)
                    return await svc.send_to_channel(s, redis, channel, "hi")
        finally:
            await redis.aclose()

    assert _run(run()) is True  # recovered after one retry


def test_send_not_configured_logs_failed():
    ch = _make_channel(name="ch")
    # No bot token set.

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                channel = await svc.get_channel(s, ch)
                ok = await svc.send_to_channel(s, redis, channel, "hi")
            async with SessionLocal() as s:
                failed = (
                    (
                        await s.execute(
                            select(NotificationLog).where(
                                NotificationLog.delivery_status == "failed"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            return ok, len(list(failed))
        finally:
            await redis.aclose()

    ok, failed = _run(run())
    assert ok is False
    assert failed == 1


def test_channel_config_is_encrypted():
    ch = _make_channel(name="ch", chat_id="-100777")

    async def fetch_raw():
        async with SessionLocal() as s:
            row = await s.get(NotificationChannel, ch)
            return row.config_enc

    raw = _run(fetch_raw())
    assert "-100777" not in raw  # chat_id stored encrypted, not plaintext
