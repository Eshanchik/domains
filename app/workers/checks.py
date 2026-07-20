"""Check actors.

For T06 this is a single dispatch actor that logs; the per-type check logic
(rdap/whois/ssl/vt/healthcheck) is added by T07+ and will branch on ``check_type``.
"""

from __future__ import annotations

import logging

import dramatiq

import app.workers.broker  # noqa: F401 — ensures the broker is configured on import

log = logging.getLogger("worker.checks")


@dramatiq.actor(max_retries=3, queue_name="checks")
def run_check(domain_id: int, check_type: str) -> None:
    """Entry point enqueued by the scheduler for a due (domain, check_type)."""
    log.info("run_check domain=%s type=%s (no-op until T07+)", domain_id, check_type)
