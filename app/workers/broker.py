"""Dramatiq broker configuration (Redis).

Imported by worker processes and by any module that defines actors. Kept separate
so importing an actor module wires the broker exactly once.
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.config import settings

redis_broker = RedisBroker(url=str(settings.redis_url))
dramatiq.set_broker(redis_broker)
