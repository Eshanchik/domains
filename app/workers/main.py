"""Placeholder worker entrypoint.

Connects to Redis and idles. Replaced by the Dramatiq worker in T06. Kept so the
``worker`` compose service is present and observably alive from the start.

Run: ``python -m app.workers.main``
"""

from __future__ import annotations

import asyncio
import logging

from app.db import get_redis
from app.log import configure_logging

log = logging.getLogger("worker")


async def _run() -> None:
    redis_client = get_redis()
    try:
        await redis_client.ping()
        log.info("worker started; redis reachable, awaiting task infra (T06)")
        while True:
            await asyncio.sleep(30)
            log.info("worker heartbeat")
    finally:
        await redis_client.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
