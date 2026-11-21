"""In-process token-bucket rate limiter.

State lives in this process, so limits are per worker. The deployment runs a
single worker (see PLESK_DEPLOYMENT_PLAN.md); move this to Postgres or Redis
before running more than one.

A per-member rate limit (issue #438 part 2) overrides both the refill rate and
the bucket capacity for a single ``allow``/``retry_after`` call, so
``allow(key, rate_override=r)`` behaves like a limiter constructed with
``RateLimiter(r)`` for that call only. Pass ``rate_override=None`` (or a value
that is not positive) to use the configured rate.

``borrow=True`` is for costs that can exceed the bucket, such as an ingest batch
charged one token per record: the call is admitted once the bucket holds
``min(cost, capacity)`` tokens and then the full ``cost`` is taken, so the
bucket can go negative and later calls wait until the debt is repaid. The
average rate is still the configured rate, and a batch larger than the bucket
is never refused forever. Without ``borrow`` a cost larger than the bucket is
always refused, as before.
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
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._max_keys = max_keys

    def _effective(self, rate_override: float | None) -> tuple[float, float]:
        """Return ``(refill_rate, capacity)`` for a call.

        A positive ``rate_override`` (per second) replaces both the refill rate
        and the bucket capacity. ``None``, zero or a negative value uses the
        configured rate and burst: an override can only be set positive through
        the admin API, and a stray non-positive value must not lock a member out
        with a bucket that never refills.
        """
        if rate_override is None or rate_override <= 0:
            return self.rate, self.burst
        return rate_override, max(rate_override, 1.0)

    def _level(self, key: str, now: float, refill_rate: float, capacity: float) -> tuple[float, float]:
        tokens, last = self._buckets.get(key, (capacity, now))
        return min(capacity, tokens + (now - last) * refill_rate), now

    def allow(self, key: str, cost: float = 1.0, now: float | None = None,
              rate_override: float | None = None, borrow: bool = False) -> bool:
        """True if ``cost`` tokens are available for ``key`` now (and take them).

        ``rate_override``: see ``_effective``. ``borrow``: see the module docstring.
        """
        now = time.monotonic() if now is None else now
        refill_rate, capacity = self._effective(rate_override)
        tokens, _ = self._level(key, now, refill_rate, capacity)
        self._buckets.pop(key, None)
        need = min(cost, capacity) if borrow else cost
        ok = tokens >= need - _EPS
        if ok:
            tokens -= cost
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return ok

    def retry_after(self, key: str, cost: float = 1.0, now: float | None = None,
                    rate_override: float | None = None, borrow: bool = False) -> float:
        """Seconds until ``allow`` with the same arguments would pass.

        0.0 when ``key`` already has enough tokens. Peeks without taking a token.
        Without ``borrow``, a cost larger than the bucket never passes and this
        returns ``math.inf``.
        """
        now = time.monotonic() if now is None else now
        refill_rate, capacity = self._effective(rate_override)
        tokens, _ = self._level(key, now, refill_rate, capacity)
        need = min(cost, capacity) if borrow else cost
        if tokens >= need - _EPS:
            return 0.0
        if need > capacity or refill_rate <= 0:
            return float("inf")
        return (need - tokens) / refill_rate
