"""Async retry with exponential backoff and jitter (SPEC §CLAUDE, NFR-3)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable


class RetryError(Exception):
    """Raised when all retry attempts are exhausted; wraps the last exception."""

    def __init__(self, attempts: int, last_exc: BaseException) -> None:
        super().__init__(f"failed after {attempts} attempts: {last_exc!r}")
        self.attempts = attempts
        self.last_exc = last_exc


def backoff_delay(attempt: int, *, base: float, factor: float, cap: float, jitter: float) -> float:
    """Delay before ``attempt`` (1-based): base*factor**(attempt-1), capped, with jitter."""
    raw = min(cap, base * (factor ** (attempt - 1)))
    return raw + random.uniform(0, jitter * raw)


async def with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    base: float = 0.5,
    factor: float = 2.0,
    cap: float = 30.0,
    jitter: float = 0.1,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Call ``fn`` up to ``retries`` times with exponential backoff.

    Re-raises non-matching exceptions immediately; raises ``RetryError`` when the
    retriable attempts are exhausted. ``sleep`` is injectable for tests.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            return await fn()
        except exceptions as exc:
            last_exc = exc
            if attempt == retries:
                break
            await sleep(backoff_delay(attempt, base=base, factor=factor, cap=cap, jitter=jitter))
    raise RetryError(retries, last_exc)  # type: ignore[arg-type]
