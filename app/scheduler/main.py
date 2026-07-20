"""Placeholder scheduler entrypoint.

Connects to Redis and idles. Replaced by the real scheduling loop in T06. Kept so
the ``scheduler`` compose service is present and observably alive from the start.

Run: ``python -m app.scheduler.main``
"""

from __future__ import annotations

import asyncio
import logging

from app.db import get_redis
from app.log import configure_logging

log = logging.getLogger("scheduler")


async def _run() -> None:
    redis_client = get_redis()
    try:
        await redis_client.ping()
        log.info("scheduler started; redis reachable, awaiting scheduling loop (T06)")
        while True:
            await asyncio.sleep(30)
            log.info("scheduler heartbeat")
    finally:
        await redis_client.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
