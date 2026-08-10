"""A per-key token-bucket rate limiter, in-memory and hand-rolled.

In-memory on purpose: it sits in front of the streaming ask endpoint as a cheap
per-user throttle, while the durable backstop against runaway spend is the
Postgres budget (see the cost ledger). Capacity and refill read from settings on
every call, so tests can retune them without rebuilding the limiter; the clock is
injectable so refill can be tested without real time passing.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from knowledge_desk.config import settings


class TokenBucketLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    # A bucket refills to full after burst/rate minutes of silence, at which
    # point it is indistinguishable from a key that has never been seen. Holding
    # it after that is pure leak: one entry per user id, or per client address,
    # kept for the life of the process.
    _EVICT_AFTER_SECONDS = 3600.0

    def reset(self) -> None:
        self._buckets.clear()

    def _evict_idle(self, now: float) -> None:
        cutoff = now - self._EVICT_AFTER_SECONDS
        for key in [k for k, (_, last) in self._buckets.items() if last < cutoff]:
            del self._buckets[key]

    def check(
        self, key: str, burst: int | None = None, per_min: int | None = None
    ) -> tuple[bool, float]:
        """Consume one token for `key`. Returns (allowed, retry_after_seconds).

        Capacity and refill default to the per-user ask limits. Callers that
        police a different surface pass their own, reading them from settings at
        call time so the values stay retunable without rebuilding the limiter.
        """
        burst = settings.rate_burst if burst is None else burst
        refill_per_sec = (settings.rate_per_min if per_min is None else per_min) / 60.0
        now = self._clock()
        if len(self._buckets) > 1000:
            # Amortized: sweeping on every call would make a cheap check O(n).
            self._evict_idle(now)
        tokens, last = self._buckets.get(key, (float(burst), now))
        tokens = min(float(burst), tokens + (now - last) * refill_per_sec)

        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return True, 0.0
        retry = (1.0 - tokens) / refill_per_sec if refill_per_sec > 0 else 60.0
        self._buckets[key] = (tokens, now)
        return False, retry


limiter = TokenBucketLimiter()

# A second bucket for the unauthenticated auth routes, keyed by caller IP rather
# than user id. Separate from `limiter` on purpose: these are the only endpoints
# an anonymous caller can reach, each one costs a bcrypt verification, and a
# password guess deserves a much tighter allowance than a question from someone
# who has already logged in.
auth_limiter = TokenBucketLimiter()
