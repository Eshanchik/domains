"""Outgoing webhook delivery: signing, filtering, encryption."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import respx

from app.db import SessionLocal, get_redis  # noqa: F401
from app.models.alert import AlertEvent
from app.models.api import WebhookEndpoint
from app.services import webhooks

HOOK = "https://example.com/hook"


def _run(coro):
    return asyncio.run(coro)


def test_sign_is_hmac_sha256():
    sig = webhooks.sign("secret", b"body")
    assert sig.startswith("sha256=")
    import hashlib
    import hmac

    assert sig == "sha256=" + hmac.new(b"secret", b"body", hashlib.sha256).hexdigest()


def _add_event(domain_id: int, kind="expiry") -> int:
    async def _c() -> int:
        async with SessionLocal() as s:
            e = AlertEvent(
                domain_id=domain_id,
                kind=kind,
                dedupe_key=f"{domain_id}:{kind}",
                severity="high",
                state="active",
                fired_at=datetime.now(UTC),
                payload_json={"days": 3},
            )
            s.add(e)
            await s.commit()
            await s.refresh(e)
            return e.id

    return _run(_c())


def test_secret_encrypted():
    async def _c():
        async with SessionLocal() as s:
            ep = await webhooks.create_endpoint(
                s, url=HOOK, secret="TOPSECRET", events=[], actor_id=None
            )
            return ep.secret_enc

    enc = _run(_c())
    assert enc and "TOPSECRET" not in enc


def test_deliver_signs_and_posts(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    eid = _add_event(dom, "expiry")

    async def setup():
        async with SessionLocal() as s:
            await webhooks.create_endpoint(s, url=HOOK, secret="s3cr3t", events=[], actor_id=None)

    _run(setup())
    router = respx.mock(assert_all_called=False)
    route = router.post(HOOK).mock(return_value=httpx.Response(200))

    async def run():
        with router:
            async with SessionLocal() as s:
                event = await s.get(AlertEvent, eid)
                n = await webhooks.deliver(s, event)
            req = route.calls.last.request  # read before respx resets on exit
            return n, req.headers.get("X-DomainGuard-Signature"), req.content

    n, sig, body = _run(run())
    payload = json.loads(body)
    assert n == 1
    assert sig == webhooks.sign("s3cr3t", body)
    assert payload["event"] == "expiry"
    assert payload["domain"] == "example.com"
    assert payload["severity"] == "high"


def test_deliver_respects_event_filter(make_company, make_project, make_domain):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    eid = _add_event(dom, "vt_malicious")

    async def setup():
        async with SessionLocal() as s:
            # Endpoint only subscribes to 'expiry' events.
            await webhooks.create_endpoint(
                s, url=HOOK, secret=None, events=["expiry"], actor_id=None
            )

    _run(setup())

    async def run():
        async with SessionLocal() as s:
            event = await s.get(AlertEvent, eid)
            return await webhooks.deliver(s, event)

    assert _run(run()) == 0  # vt_malicious not in the endpoint's filter


def test_delete_endpoint():
    async def run():
        async with SessionLocal() as s:
            ep = await webhooks.create_endpoint(s, url=HOOK, secret=None, events=[], actor_id=None)
            await webhooks.delete_endpoint(s, ep, actor_id=None)
        async with SessionLocal() as s:
            from sqlalchemy import func, select

            return (await s.execute(select(func.count()).select_from(WebhookEndpoint))).scalar_one()

    assert _run(run()) == 0
