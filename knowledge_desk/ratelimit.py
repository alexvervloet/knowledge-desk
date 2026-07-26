"""A per-key token-bucket rate limiter, in-memory and hand-rolled.

In-memory on purpose: it sits in front of the streaming ask endpoint as a cheap
per-user throttle, while the durable backstop against runaway spend is the
Postgres budget (see the cost ledger). Capacity and refill read from settings on
every call, so tests can retune them without rebuilding the limiter; the clock is
injectable so refill can be tested without real time passing.
"""

from __future__ import annotations

import time
from typing import Callable

from knowledge_desk.config import settings


class TokenBucketLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def reset(self) -> None:
        self._buckets.clear()

    def check(self, key: str) -> tuple[bool, float]:
        """Consume one token for `key`. Returns (allowed, retry_after_seconds)."""
        burst = settings.rate_burst
        refill_per_sec = settings.rate_per_min / 60.0
        now = self._clock()
        tokens, last = self._buckets.get(key, (float(burst), now))
        tokens = min(float(burst), tokens + (now - last) * refill_per_sec)

        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return True, 0.0
        retry = (1.0 - tokens) / refill_per_sec if refill_per_sec > 0 else 60.0
        self._buckets[key] = (tokens, now)
        return False, retry


limiter = TokenBucketLimiter()
