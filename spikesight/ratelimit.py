"""A deliberately conservative outbound rate limiter.

Riot's edge endpoints have no published rate limit for client traffic, so the
only responsible policy is to stay well under anything a real client would do.
Every remote request in SpikeSight passes through one shared limiter: a token
bucket for average rate, plus a semaphore capping in-flight requests.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, rate_per_second: float, burst: int, max_concurrency: int) -> None:
        self._rate = max(0.1, float(rate_per_second))
        self._capacity = max(1.0, float(burst))
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self.total_requests = 0

    async def _take_token(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self.total_requests += 1
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self._rate
            await asyncio.sleep(min(wait, 1.0))

    def slot(self) -> "_Slot":
        return _Slot(self)

    @property
    def in_flight_capacity(self) -> int:
        return self._semaphore._value  # noqa: SLF001 - diagnostics only


class _Slot:
    """Async context manager: acquire concurrency slot, then a rate token."""

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    async def __aenter__(self) -> None:
        await self._limiter._semaphore.acquire()  # noqa: SLF001
        try:
            await self._limiter._take_token()  # noqa: SLF001
        except BaseException:
            self._limiter._semaphore.release()  # noqa: SLF001
            raise

    async def __aexit__(self, *exc_info) -> None:
        self._limiter._semaphore.release()  # noqa: SLF001
