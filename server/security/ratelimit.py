"""In-process token-bucket rate limiter.

State lives in this process, so limits are per worker. The deployment runs a
single worker (see PLESK_DEPLOYMENT_PLAN.md); move this to Postgres or Redis
before running more than one.

A per-member rate limit (issue #438 part 2) overrides both the refill rate and
the bucket capacity for a single ``allow``/``retry_after`` call, so
``allow(key, rate_override=r)`` behaves exactly like a limiter constructed with
``RateLimiter(r)`` for that call only: a member faster than ``r`` records/s is
capped at ``r`` (burst = ``r``), and a slower member keeps an even tighter
budget. Pass ``rate_override=None`` to use the configured rate.
"""
from __future__ import annotations

import time
from collections import OrderedDict

# Tolerance so that "exactly one token has refilled" isn't lost to binary
# floating-point error (100/s * 0.01 s = 0.9999999999999999, not 1.0). It is
# also large enough that a Retry-After value is honored on the next attempt.
_EPS = 1e-6


class RateLimiter:
    def __init__(self, rate: float, burst: float | None = None, per: float = 1.0, max_keys: int = 10_000) -> None:
        self.rate = rate / per          # tokens per second
        self.burst = burst if burst is not None else max(rate, 1.0)
        self._buckets: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
        self._max_keys = max_keys

    def _effective(self, rate_override: float | None) -> tuple[float, float]:
        """Return ``(refill_rate, capacity)`` for a call.

        ``rate_override`` (records/s) replaces both the refill rate and the
        bucket capacity, so ``RateLimiter(r)``-style behaviour for that key: a
        member at ``r`` records/s has a burst of ``r`` and refills at ``r``. A
        ``None`` override uses the configured rate and burst. A ``rate_override``
        of 0 or less never refills (capacity 0), so such a call is always refused
        once its initial grant is spent.
        """
        if rate_override is None:
            return self.rate, self.burst
        if rate_override <= 0:
            return 0.0, 0.0
        return rate_override, max(rate_override, 1.0)

    def allow(self, key: str, cost: float = 1.0, now: float | None = None,
              rate_override: float | None = None) -> bool:
        """True if ``cost`` tokens are available for ``key`` now.

        ``rate_override`` (records/s, e.g. a per-member ingest limit) replaces
        this key's refill rate and bucket capacity for this call. When None the
        configured rate and capacity apply.
        """
        now = time.monotonic() if now is None else now
        refill_rate, capacity = self._effective(rate_override)
        tokens, last = self._buckets.pop(key, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * refill_rate)
        ok = tokens >= cost - _EPS
        if ok:
            tokens -= cost
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return ok

    def retry_after(self, key: str, cost: float = 1.0, now: float | None = None,
                    rate_override: float | None = None) -> float:
        """Seconds until ``allow(key, cost, now, rate_override)`` would pass.

        0.0 when ``key`` already has enough tokens. Peeks without consuming a
        token, so a retry scheduled at this instant is actually granted (the
        allow() tolerance makes the boundary exact rather than off by
        floating-point error).
        """
        now = time.monotonic() if now is None else now
        refill_rate, capacity = self._effective(rate_override)
        tokens, last = self._buckets.get(key, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * refill_rate)
        if tokens >= cost - _EPS:
            return 0.0
        return (cost - tokens) / refill_rate
