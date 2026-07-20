"""Prometheus metrics endpoint (SPEC NFR-5).

Exposes a small set of gauges computed on scrape: domain/alert counts and, per
external service, circuit-breaker state and recent failure counts (a proxy for
external-API error rates). Written as plain exposition text to avoid a client dep.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import redis_dep
from app.models.alert import AlertEvent
from app.models.domain import Domain

router = APIRouter(tags=["metrics"])

SERVICES = ("rdap", "whois", "ssl", "vt", "telegram", "namecheap")
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _line(name: str, value, labels: str = "") -> str:
    return f"{name}{labels} {value}"


@router.get("/metrics")
async def metrics(
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(redis_dep),
) -> Response:
    domains_total = (await session.execute(select(func.count()).select_from(Domain))).scalar_one()
    active_alerts = (
        await session.execute(
            select(func.count()).select_from(AlertEvent).where(AlertEvent.state == "active")
        )
    ).scalar_one()

    lines: list[str] = [
        "# HELP dg_domains_total Total domains in the registry.",
        "# TYPE dg_domains_total gauge",
        _line("dg_domains_total", domains_total),
        "# HELP dg_active_alerts_total Active (unresolved) alert events.",
        "# TYPE dg_active_alerts_total gauge",
        _line("dg_active_alerts_total", active_alerts),
        "# HELP dg_circuit_breaker_open Circuit breaker open (1) or closed (0).",
        "# TYPE dg_circuit_breaker_open gauge",
    ]
    for svc in SERVICES:
        is_open = 1 if await redis.get(f"cb:{svc}:open_until") is not None else 0
        lines.append(_line("dg_circuit_breaker_open", is_open, f'{{service="{svc}"}}'))
    lines.append("# HELP dg_circuit_breaker_failures Consecutive failures per service.")
    lines.append("# TYPE dg_circuit_breaker_failures gauge")
    for svc in SERVICES:
        fails = await redis.get(f"cb:{svc}:fails")
        lines.append(_line("dg_circuit_breaker_failures", int(fails or 0), f'{{service="{svc}"}}'))

    return Response("\n".join(lines) + "\n", media_type=CONTENT_TYPE)
