"""VirusTotal domain reputation check (SPEC FR-CK-3).

Free key budget: 4 requests/minute and 500/day. A per-minute token bucket plus a
daily budget counter (both in Redis) keep the whole fleet within the free tier.
The API key is stored encrypted in Settings. A malicious detection produces a
``fail`` CheckResult (the alerter consumes it in T12).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.check_result_store import write_result
from app.core import rate_limiter
from app.core.circuit_breaker import CircuitBreaker
from app.models.check_result import CheckStatus
from app.models.domain import Domain
from app.models.vt_result import VtResult
from app.services import settings_store

log = logging.getLogger("checks.vt")

VT_URL = "https://www.virustotal.com/api/v3/domains/{fqdn}"
PER_MIN_CAPACITY = 4.0
PER_MIN_REFILL = 4.0 / 60.0
DAILY_LIMIT = 500


class VtError(Exception):
    """Transient VT failure (429, 5xx, timeout, bad key) — mark stale/defer."""


class VtParsed:
    def __init__(self, payload: dict[str, Any]) -> None:
        attrs = (payload.get("data") or {}).get("attributes") or {}
        stats = attrs.get("last_analysis_stats") or {}
        self.harmless = int(stats.get("harmless", 0))
        self.malicious = int(stats.get("malicious", 0))
        self.suspicious = int(stats.get("suspicious", 0))
        self.undetected = int(stats.get("undetected", 0))
        self.reputation = int(attrs.get("reputation", 0))
        self.categories = attrs.get("categories") or {}
        last = attrs.get("last_analysis_date")
        self.last_analysis_at = (
            datetime.fromtimestamp(last, tz=UTC) if isinstance(last, int | float) else None
        )


def _seconds_until_utc_midnight(now: datetime) -> int:
    tomorrow = now.date().toordinal() + 1
    midnight = datetime.fromordinal(tomorrow).replace(tzinfo=UTC)
    return max(1, int((midnight - now).total_seconds()))


async def query_vt(
    client: httpx.AsyncClient, api_key: str, fqdn: str, *, timeout: float = 15.0
) -> VtParsed:
    try:
        resp = await client.get(
            VT_URL.format(fqdn=fqdn), headers={"x-apikey": api_key}, timeout=timeout
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise VtError(f"vt request failed: {exc}") from exc

    if resp.status_code == 429:
        raise VtError("vt rate limited (429)")
    if resp.status_code in (401, 403):
        raise VtError(f"vt auth error ({resp.status_code})")
    if resp.status_code >= 500:
        raise VtError(f"vt server error ({resp.status_code})")
    if resp.status_code == 404:
        return VtParsed({})  # unknown domain — treated as no detections
    if resp.status_code >= 400:
        raise VtError(f"vt status {resp.status_code}")
    return VtParsed(resp.json())


def _status_for(parsed: VtParsed) -> str:
    if parsed.malicious >= 1:
        return CheckStatus.fail
    if parsed.suspicious >= 1:
        return CheckStatus.warn
    return CheckStatus.ok


async def run_vt_check(
    session: AsyncSession,
    redis: aioredis.Redis,
    domain_id: int,
    *,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Run the VT check for a domain, respecting the free-tier budget."""
    ts = now or datetime.now(UTC)
    domain = await session.get(Domain, domain_id)
    if domain is None:
        return "missing"

    api_key = await settings_store.get_secret(session, settings_store.VT_API_KEY)
    if not api_key:
        return "not_configured"

    allowed, _retry = await rate_limiter.acquire_token(
        redis,
        rate_limiter.service_key("vt"),
        capacity=PER_MIN_CAPACITY,
        refill_rate=PER_MIN_REFILL,
        now=ts.timestamp(),
    )
    if not allowed:
        return "rate_limited"

    day = ts.strftime("%Y%m%d")
    within_budget = await rate_limiter.check_daily_budget(
        redis,
        rate_limiter.daily_key("vt", day),
        limit=DAILY_LIMIT,
        ttl_seconds=_seconds_until_utc_midnight(ts),
    )
    if not within_budget:
        return "budget_exhausted"

    breaker = CircuitBreaker(redis, "vt")
    if not await breaker.allow():
        return "circuit_open"

    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        parsed = await query_vt(client, api_key, domain.fqdn)
        await breaker.record_success()
    except VtError as exc:
        await breaker.record_failure()
        log.warning("vt check stale domain=%s: %s", domain_id, exc)
        await write_result(
            session,
            domain_id=domain_id,
            check_type="vt",
            status=CheckStatus.stale,
            data={"error": str(exc)},
            checked_at=ts,
        )
        await session.commit()
        return CheckStatus.stale
    finally:
        if owns_client:
            await client.aclose()

    session.add(
        VtResult(
            domain_id=domain_id,
            harmless=parsed.harmless,
            malicious=parsed.malicious,
            suspicious=parsed.suspicious,
            undetected=parsed.undetected,
            reputation=parsed.reputation,
            categories_json=parsed.categories,
            last_analysis_at=parsed.last_analysis_at,
            checked_at=ts,
        )
    )
    status = _status_for(parsed)
    await write_result(
        session,
        domain_id=domain_id,
        check_type="vt",
        status=status,
        data={
            "malicious": parsed.malicious,
            "suspicious": parsed.suspicious,
            "harmless": parsed.harmless,
            "reputation": parsed.reputation,
        },
        checked_at=ts,
    )
    await session.commit()
    return status
