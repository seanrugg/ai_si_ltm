"""Unit tests for ``server.security.ratelimit.RateLimiter``.

The limiter is time-controlled via the ``now=`` argument — no ``sleep`` is used.
``ratelimit.py`` itself is not changed.
"""
from __future__ import annotations

from server.security.ratelimit import RateLimiter

START = 1000.0


def test_fresh_key_gets_burst_then_refused():
    lim = RateLimiter(rate=10, burst=5)
    now = START
    for _ in range(5):
        assert lim.allow("k", now=now) is True
    assert lim.allow("k", now=now) is False


def test_tokens_refill_at_rate_per_second():
    lim = RateLimiter(rate=10, burst=1)  # 10 tokens/s, bucket holds 1
    now = START
    assert lim.allow("k", now=now) is True   # consume the single token
    assert lim.allow("k", now=now) is False  # refused
    # 0.1 s later → exactly 1 token refilled
    assert lim.allow("k", now=now + 0.1) is True
    assert lim.allow("k", now=now + 0.1) is False


def test_keys_are_independent():
    lim = RateLimiter(rate=10, burst=2)
    now = START
    lim.allow("a", now=now)
    lim.allow("a", now=now)
    assert lim.allow("a", now=now) is False  # a drained
    assert lim.allow("b", now=now) is True   # b untouched
    assert lim.allow("b", now=now) is True


def test_enrollment_setting():
    lim = RateLimiter(5, per=3600)  # 5 tokens per hour, burst 5
    now = START
    for _ in range(5):
        assert lim.allow("k", now=now) is True
    assert lim.allow("k", now=now) is False
    # 720 s later → 720 * 5/3600 = 1 token
    assert lim.allow("k", now=now + 720) is True
    assert lim.allow("k", now=now + 720) is False


def test_cost_uses_multiple_tokens():
    lim = RateLimiter(rate=10, burst=10)
    now = START
    assert lim.allow("k", cost=3, now=now) is True   # 10 -> 7
    assert lim.allow("k", cost=7, now=now) is True   # 7 -> 0
    assert lim.allow("k", cost=1, now=now) is False  # empty

    # a cost bigger than the bucket is always refused
    lim2 = RateLimiter(rate=10, burst=5)
    assert lim2.allow("k", cost=6, now=now) is False


def test_tokens_never_exceed_burst():
    lim = RateLimiter(rate=100, burst=5)
    now = START
    for _ in range(5):
        assert lim.allow("k", now=now) is True   # drain to 0
    # idle for a long time — refill is capped at burst
    assert lim.allow("k", now=now + 1000) is True  # refilled to 5
    for _ in range(4):
        assert lim.allow("k", now=now + 1000) is True
    assert lim.allow("k", now=now + 1000) is False


def test_lru_eviction():
    lim = RateLimiter(rate=10, burst=5, max_keys=3)
    now = START
    lim.allow("a", now=now)
    lim.allow("b", now=now)
    lim.allow("c", now=now)
    # touch "a" so it becomes most-recently-used
    lim.allow("a", now=now)
    # adding "d" must evict "b" (least recently used), not "a"
    lim.allow("d", now=now)
    # "b" comes back with a fresh, full bucket
    assert lim.allow("b", now=now) is True
    for _ in range(4):
        assert lim.allow("b", now=now) is True
    assert lim.allow("b", now=now) is False
    # "a" (recently used) is still present: adding "e" evicts "c", not "a"
    lim.allow("e", now=now)
    assert lim.allow("a", now=now) is True
