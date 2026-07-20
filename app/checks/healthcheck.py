"""Custom health-check execution + up/down state machine (SPEC FR-CK-4).

Success = HTTP status is expected AND (if set) the Location header matches AND
(if set) the body substring is present. N consecutive failures flip the check to
``down`` (a "down" transition); the next success flips it back (a "recovered"
transition). Flapping below the threshold does not transition.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.healthcheck import HealthCheck, HealthCheckResult

log = logging.getLogger("checks.healthcheck")


def status_matches(code: int, spec: str) -> bool:
    """Match a status code against a spec like ``"301,302"`` or ``"200-299"``."""
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            if lo.strip().isdigit() and hi.strip().isdigit() and int(lo) <= code <= int(hi):
                return True
        elif part.isdigit() and int(part) == code:
            return True
    return False


def pattern_matches(value: str | None, pattern: str) -> bool:
    """Match ``value`` against ``pattern`` as a regex, falling back to substring."""
    if value is None:
        return False
    try:
        return re.search(pattern, value) is not None
    except re.error:
        return pattern in value


@dataclass
class HealthOutcome:
    ok: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None
    transition: str | None  # "down" | "recovered" | None
    state: str
    consecutive_failures: int


async def _perform(
    hc: HealthCheck, client: httpx.AsyncClient
) -> tuple[bool, int | None, int | None, str | None]:
    start = time.monotonic()
    try:
        resp = await client.request(
            hc.method.upper(),
            hc.url,
            follow_redirects=hc.follow_redirects,
            timeout=hc.timeout_s,
        )
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError) as exc:
        return False, None, None, f"request failed: {exc}"

    latency = int((time.monotonic() - start) * 1000)
    ok = status_matches(resp.status_code, hc.expected_statuses)
    reason = None if ok else f"unexpected status {resp.status_code}"

    if (
        ok
        and hc.location_pattern
        and not pattern_matches(resp.headers.get("location"), hc.location_pattern)
    ):
        ok, reason = False, "location pattern mismatch"
    if ok and hc.body_substring:
        try:
            body = resp.text
        except Exception:  # noqa: BLE001
            body = ""
        if hc.body_substring not in body:
            ok, reason = False, "body substring not found"
    return ok, resp.status_code, latency, reason


async def run_healthcheck(
    session: AsyncSession,
    redis: aioredis.Redis,  # noqa: ARG001 — kept for a uniform check signature
    healthcheck_id: int,
    *,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> HealthOutcome:
    ts = now or datetime.now(UTC)
    hc = await session.get(HealthCheck, healthcheck_id)
    if hc is None or not hc.is_enabled:
        return HealthOutcome(False, None, None, "disabled", None, "unknown", 0)

    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        ok, status_code, latency, error = await _perform(hc, client)
    finally:
        if owns_client:
            await client.aclose()

    session.add(
        HealthCheckResult(
            healthcheck_id=hc.id,
            status_code=status_code,
            ok=ok,
            latency_ms=latency,
            error=error,
            checked_at=ts,
        )
    )

    transition: str | None = None
    prev_state = hc.state
    if ok:
        hc.consecutive_failures = 0
        hc.state = "up"
        if prev_state == "down":
            transition = "recovered"
    else:
        hc.consecutive_failures += 1
        if hc.consecutive_failures >= hc.fail_threshold and prev_state != "down":
            hc.state = "down"
            transition = "down"

    hc.last_checked_at = ts
    hc.next_check_at = ts + timedelta(minutes=hc.interval_min)
    await session.commit()

    if transition:
        log.info("healthcheck %s transition → %s", hc.id, transition)
    return HealthOutcome(
        ok, status_code, latency, error, transition, hc.state, hc.consecutive_failures
    )
