"""Expiry check: RDAP primary, WHOIS fallback (SPEC FR-CK-1, FR-CK-6).

Resilience: token-bucket rate limit + circuit breaker + retry. External failures
mark the check ``stale`` and never wipe existing domain data. Registry fields with
a ``manual`` source are not overwritten (SPEC merge rules).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks import rdap as rdap_mod
from app.checks import whois as whois_mod
from app.checks.check_result_store import write_result
from app.checks.rdap import RdapData, RdapError, RdapNotFound
from app.checks.whois import WhoisError
from app.core import rate_limiter
from app.core.circuit_breaker import CircuitBreaker
from app.core.retry import RetryError, with_retry
from app.models.check_result import CheckStatus
from app.models.domain import Domain, DomainFieldHistory

log = logging.getLogger("checks.expiry")

RDAP_CAPACITY = 5.0
RDAP_REFILL = 5.0
TRACKED = ("expiry_date", "epp_statuses", "nameservers", "registrant")


async def _fetch_rdap(
    client: httpx.AsyncClient, redis: aioredis.Redis, fqdn: str, tld: str
) -> RdapData:
    bootstrap = await rdap_mod.load_bootstrap(client, redis)
    base = rdap_mod.base_for_tld(bootstrap, tld)
    if base is None:
        raise RdapNotFound(f"no rdap server for .{tld}")
    payload = await rdap_mod.query_domain(client, base, fqdn)
    return rdap_mod.parse_rdap(payload)


def _apply_registry_data(domain: Domain, data: RdapData, source: str) -> list[DomainFieldHistory]:
    """Apply fetched registry data, skipping manual fields; return history rows."""
    field_sources = dict(domain.field_sources or {})
    history: list[DomainFieldHistory] = []

    def _set(field: str, new_value: Any) -> None:
        if new_value is None or new_value == [] or new_value == "":
            return
        if field_sources.get(field) == "manual":
            return  # manual wins over autosync
        old_value = getattr(domain, field)
        if old_value == new_value:
            return
        if field in TRACKED:
            history.append(
                DomainFieldHistory(
                    domain_id=domain.id,
                    field=field,
                    old=_fmt(old_value),
                    new=_fmt(new_value),
                    source=source,
                )
            )
        setattr(domain, field, new_value)
        field_sources[field] = source

    _set("expiry_date", data.expiry_date)
    _set("registration_date", data.registration_date)
    _set("updated_date", data.updated_date)
    _set("epp_statuses", data.statuses)
    _set("nameservers", data.nameservers)
    _set("registrant", data.registrant)
    domain.field_sources = field_sources
    return history


def _fmt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _serialize(data: RdapData) -> dict[str, Any]:
    return {
        "expiry_date": _fmt(data.expiry_date),
        "registration_date": _fmt(data.registration_date),
        "statuses": data.statuses,
        "nameservers": data.nameservers,
        "registrant": data.registrant,
    }


async def run_expiry_check(
    session: AsyncSession,
    redis: aioredis.Redis,
    domain_id: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Run the expiry check for a domain. Returns the resulting status string."""
    domain = await session.get(Domain, domain_id)
    if domain is None:
        return "missing"

    allowed, _retry = await rate_limiter.acquire_token(
        redis, rate_limiter.service_key("rdap"), capacity=RDAP_CAPACITY, refill_rate=RDAP_REFILL
    )
    if not allowed:
        log.info("rdap rate-limited, deferring domain=%s", domain_id)
        return "rate_limited"

    breaker = CircuitBreaker(redis, "rdap")
    owns_client = client is None
    client = client or httpx.AsyncClient()
    data: RdapData | None = None
    source = "rdap"

    try:
        if await breaker.allow():
            try:
                data = await with_retry(
                    lambda: _fetch_rdap(client, redis, domain.fqdn, domain.tld),
                    retries=3,
                    exceptions=(RdapError,),
                )
                await breaker.record_success()
            except (RetryError, RdapError):
                await breaker.record_failure()
            except RdapNotFound:
                pass  # fall through to WHOIS

        if data is None:
            # Fallback to WHOIS.
            try:
                data = await whois_mod.query_whois(domain.fqdn)
                source = "whois"
            except WhoisError as exc:
                log.warning("expiry check stale domain=%s: %s", domain_id, exc)
                await write_result(
                    session,
                    domain_id=domain_id,
                    check_type="rdap",
                    status=CheckStatus.stale,
                    data={"error": str(exc)},
                )
                await session.commit()
                return CheckStatus.stale
    finally:
        if owns_client:
            await client.aclose()

    history = _apply_registry_data(domain, data, source)
    for h in history:
        session.add(h)
    await write_result(
        session,
        domain_id=domain_id,
        check_type="rdap",
        status=CheckStatus.ok,
        data={"source": source, **_serialize(data)},
    )
    await session.commit()
    return CheckStatus.ok
