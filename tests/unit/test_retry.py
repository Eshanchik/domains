"""Async retry with exponential backoff."""

from __future__ import annotations

import pytest

from app.core.retry import RetryError, backoff_delay, with_retry


async def _noop_sleep(_delay: float) -> None:
    return None


async def test_returns_on_first_success() -> None:
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    assert await with_retry(fn, sleep=_noop_sleep) == "ok"
    assert len(calls) == 1


async def test_retries_then_succeeds() -> None:
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("transient")
        return "ok"

    assert await with_retry(fn, retries=5, sleep=_noop_sleep) == "ok"
    assert len(calls) == 3


async def test_exhausts_and_raises_retryerror() -> None:
    calls = []

    async def fn():
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RetryError) as exc:
        await with_retry(fn, retries=3, sleep=_noop_sleep)
    assert exc.value.attempts == 3
    assert len(calls) == 3
    assert isinstance(exc.value.last_exc, RuntimeError)


async def test_non_matching_exception_propagates_immediately() -> None:
    calls = []

    async def fn():
        calls.append(1)
        raise KeyError("nope")

    with pytest.raises(KeyError):
        await with_retry(fn, retries=5, exceptions=(ValueError,), sleep=_noop_sleep)
    assert len(calls) == 1  # not retried


def test_backoff_grows_and_caps() -> None:
    d1 = backoff_delay(1, base=1, factor=2, cap=10, jitter=0)
    d2 = backoff_delay(2, base=1, factor=2, cap=10, jitter=0)
    d5 = backoff_delay(5, base=1, factor=2, cap=10, jitter=0)
    assert d1 == 1 and d2 == 2
    assert d5 == 10  # capped
