"""Scheduler process: periodically backfill schedules and enqueue due checks.

Run: ``python -m app.scheduler.main``
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import SessionLocal, get_redis
from app.log import configure_logging
from app.scheduler.service import (
    backfill_schedules,
    enqueue_due,
    enqueue_due_healthchecks,
    enqueue_due_registrar_syncs,
)

KYIV = ZoneInfo(settings.timezone)

log = logging.getLogger("scheduler")

TICK_SECONDS = 30
BACKFILL_EVERY_TICKS = 10  # backfill roughly every 5 minutes


async def _run_digests(session, redis) -> list[int]:
    from app.services.digest import run_digests
    from app.workers.checks import send_notification

    now_kyiv = datetime.now(KYIV)
    return await run_digests(
        session,
        redis,
        now_kyiv=now_kyiv,
        send=lambda cid, text: send_notification.send(cid, text, None),
    )


async def _run() -> None:
    redis = get_redis()
    tick = 0
    log.info("scheduler started")
    try:
        while True:
            try:
                async with SessionLocal() as session:
                    if tick % BACKFILL_EVERY_TICKS == 0:
                        created = await backfill_schedules(session)
                        if created:
                            log.info("backfilled %d schedules", created)
                    dispatched = await enqueue_due(session, redis)
                    hc_dispatched = await enqueue_due_healthchecks(session, redis)
                    synced = await enqueue_due_registrar_syncs(session, redis)
                    digested = await _run_digests(session, redis)
                if dispatched:
                    log.info("enqueued %d checks", len(dispatched))
                if hc_dispatched:
                    log.info("enqueued %d health-checks", len(hc_dispatched))
                if synced:
                    log.info("enqueued %d registrar syncs", len(synced))
                if digested:
                    log.info("sent %d digests", len(digested))
            except Exception:  # noqa: BLE001 — never let the loop die
                log.exception("scheduler tick failed")
            tick += 1
            await asyncio.sleep(TICK_SECONDS)
    finally:
        await redis.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
