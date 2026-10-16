"""In-process token-bucket rate limiter.

State lives in this process, so limits are per worker. The deployment runs a
single worker (see PLESK_DEPLOYMENT_PLAN.md); move this to Postgres or Redis
before running more than one.
"""
from __future__ import annotations

import time
from collections import OrderedDict


class RateLimiter:
    def __init__(self, rate: float, burst: float | None = None, per: float = 1.0, max_keys: int = 10_000) -> None:
        self.rate = rate / per          # tokens per second
        self.burst = burst if burst is not None else max(rate, 1.0)
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._max_keys = max_keys

    def allow(self, key: str, cost: float = 1.0, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, last = self._buckets.pop(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        ok = tokens >= cost
        if ok:
            tokens -= cost
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return ok
