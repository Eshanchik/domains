"""Outgoing webhooks on alert events (SPEC T21).

Admin registers endpoint URLs (with an optional HMAC secret). When an alert event
fires, the payload is POSTed to each active endpoint whose event filter matches,
signed with ``X-DomainGuard-Signature: sha256=<hmac>``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.audit import record_audit
from app.models.alert import AlertEvent
from app.models.api import WebhookEndpoint
from app.models.domain import Domain

log = logging.getLogger("services.webhooks")


async def list_endpoints(session: AsyncSession) -> list[WebhookEndpoint]:
    result = await session.execute(select(WebhookEndpoint).order_by(WebhookEndpoint.id))
    return list(result.scalars().all())


async def get_endpoint(session: AsyncSession, endpoint_id: int) -> WebhookEndpoint | None:
    return await session.get(WebhookEndpoint, endpoint_id)


async def create_endpoint(
    session: AsyncSession, *, url: str, secret: str | None, events: list[str], actor_id: int
) -> WebhookEndpoint:
    endpoint = WebhookEndpoint(
        url=url,
        secret_enc=crypto.encrypt(secret) if secret else None,
        events=[e.strip() for e in events if e.strip()],
        is_active=True,
    )
    session.add(endpoint)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        diff={"url": url},
    )
    await session.commit()
    await session.refresh(endpoint)
    return endpoint


async def delete_endpoint(
    session: AsyncSession, endpoint: WebhookEndpoint, *, actor_id: int
) -> None:
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        diff={"url": endpoint.url},
    )
    await session.delete(endpoint)
    await session.commit()


def _endpoint_secret(endpoint: WebhookEndpoint) -> str | None:
    if not endpoint.secret_enc:
        return None
    try:
        return crypto.decrypt(endpoint.secret_enc)
    except crypto.CryptoError:
        return None


def build_payload(event: AlertEvent, fqdn: str) -> dict:
    return {
        "event": event.kind,
        "severity": event.severity,
        "domain": fqdn,
        "domain_id": event.domain_id,
        "state": event.state,
        "fired_at": event.fired_at.isoformat() if event.fired_at else None,
        "payload": event.payload_json or {},
    }


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def deliver(
    session: AsyncSession, event: AlertEvent, *, client: httpx.AsyncClient | None = None
) -> int:
    """POST ``event`` to all matching active endpoints. Returns count attempted."""
    endpoints = [
        e
        for e in await list_endpoints(session)
        if e.is_active and (not e.events or event.kind in e.events)
    ]
    if not endpoints:
        return 0
    domain = await session.get(Domain, event.domain_id)
    fqdn = domain.fqdn if domain else str(event.domain_id)
    body = json.dumps(build_payload(event, fqdn)).encode()

    owns = client is None
    client = client or httpx.AsyncClient()
    attempted = 0
    try:
        for endpoint in endpoints:
            headers = {"Content-Type": "application/json"}
            secret = _endpoint_secret(endpoint)
            if secret:
                headers["X-DomainGuard-Signature"] = sign(secret, body)
            try:
                await client.post(endpoint.url, content=body, headers=headers, timeout=15.0)
                attempted += 1
            except httpx.HTTPError as exc:
                log.warning("webhook %s delivery failed: %s", endpoint.id, exc)
    finally:
        if owns:
            await client.aclose()
    return attempted
