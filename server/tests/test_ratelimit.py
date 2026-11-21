"""Unit tests for ``server.security.ratelimit.RateLimiter``.

The limiter is time-controlled via the ``now=`` argument — no ``sleep`` is used.
"""
from __future__ import annotations

import pytest

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
    # "a" survived: it still has exactly 3 of its 5 tokens (an evicted key would have 5)
    for _ in range(3):
        assert lim.allow("a", now=now) is True
    assert lim.allow("a", now=now) is False
    # "b" was evicted: it comes back with a full bucket of 5 (it had 4 left)
    for _ in range(5):
        assert lim.allow("b", now=now) is True
    assert lim.allow("b", now=now) is False


def test_override_caps_capacity_like_a_fresh_limiter():
    # An override sets both the refill rate and the bucket capacity, so
    # ``allow(k, rate_override=r)`` behaves like a RateLimiter(r) for that call.
    # A member faster than the override is therefore *capped* at the override.
    lim = RateLimiter(rate=1000)  # configured 1000/s, capacity 1000
    now = START
    assert lim.allow("k", now=now) is True
    assert lim.allow("k", now=now) is True          # configured capacity is huge
    # Override to 1/s: bucket holds 1, so the second call is refused.
    assert lim.allow("k", rate_override=1, now=now) is True
    assert lim.allow("k", rate_override=1, now=now) is False


def test_override_faster_allows_more_than_configured():
    # A faster override lets a member exceed the configured burst, because the
    # override also raises the bucket capacity for that member's key.
    now = START
    capped = RateLimiter(rate=1, burst=1)  # configured 1/s
    assert capped.allow("k", now=now) is True
    assert capped.allow("k", now=now) is False  # configured burst is 1
    faster = RateLimiter(rate=1, burst=1)  # same configured 1/s
    for _ in range(100):  # but 100/s override → 100 tokens to start
        assert faster.allow("k", rate_override=100, now=now) is True
    assert faster.allow("k", rate_override=100, now=now) is False  # 100 drained


def test_retry_after_reports_wait_seconds():
    lim = RateLimiter(rate=10, burst=1)  # 10 tokens/s, bucket holds 1
    now = START
    assert lim.allow("k", now=now) is True   # consume the single token
    assert lim.allow("k", now=now) is False  # empty
    # One token at 10/s refills in 0.1 s.
    assert lim.retry_after("k", now=now) == pytest.approx(0.1)
    # A retry scheduled exactly that long later is granted (the allow() tolerance
    # makes the boundary exact rather than off by floating-point error).
    assert lim.allow("k", now=now + 0.1) is True


def test_override_retry_after_tracks_the_override_rate():
    lim = RateLimiter(rate=10, burst=1)  # configured 10/s, bucket holds 1
    now = START
    assert lim.allow("k", rate_override=1, now=now) is True  # override capacity 1
    assert lim.allow("k", rate_override=1, now=now) is False  # empty at 1/s
    # One token at 1/s refills in 1.0 s.
    assert lim.retry_after("k", now=now, rate_override=1) == pytest.approx(1.0)


def test_borrow_admits_a_batch_bigger_than_the_bucket_then_repays():
    lim = RateLimiter(rate=10, burst=10)
    now = START
    assert lim.allow("k", cost=25, now=now) is False             # without borrow: never
    assert lim.retry_after("k", cost=25, now=now) == float("inf")
    assert lim.allow("k", cost=25, now=now, borrow=True) is True  # 10 -> -15
    assert lim.allow("k", now=now, borrow=True) is False
    # 16 tokens needed at 10/s.
    assert lim.retry_after("k", now=now, borrow=True) == pytest.approx(1.6)
    assert lim.allow("k", now=now + 1.6, borrow=True) is True


def test_borrow_waits_for_a_full_bucket_before_a_big_batch():
    lim = RateLimiter(rate=10, burst=10)
    now = START
    assert lim.allow("k", cost=5, now=now, borrow=True) is True   # 10 -> 5
    assert lim.allow("k", cost=50, now=now, borrow=True) is False  # needs the full 10
    assert lim.retry_after("k", cost=50, now=now, borrow=True) == pytest.approx(0.5)


@pytest.mark.parametrize("override", [0, -3])
def test_non_positive_override_uses_the_configured_rate(override):
    lim = RateLimiter(rate=10, burst=2)
    now = START
    assert lim.allow("k", now=now, rate_override=override) is True
    assert lim.allow("k", now=now, rate_override=override) is True
    assert lim.allow("k", now=now, rate_override=override) is False
    assert lim.retry_after("k", now=now, rate_override=override) == pytest.approx(0.1)


def test_retry_after_with_zero_configured_rate_is_infinite():
    lim = RateLimiter(rate=0, burst=1)
    now = START
    assert lim.allow("k", now=now) is True
    assert lim.allow("k", now=now) is False
    assert lim.retry_after("k", now=now) == float("inf")
