"""Per-wake budget (brain calls, tokens, seconds) and a daily write cap.

The daily count lives in the mind's state file, keyed by the local date, so it
survives between wakes and restarts.
"""
from __future__ import annotations

import time
from typing import Callable


class Budget:
    def __init__(self, *, calls: int, tokens: int, seconds: float, daily_writes: int,
                 written_today: int = 0, clock: Callable[[], float] = time.monotonic) -> None:
        self.calls_left, self.tokens_left = calls, tokens
        self.daily_writes, self.written_today = daily_writes, written_today
        self.clock = clock
        self.deadline = clock() + seconds
        self.calls_used = self.tokens_used = 0

    def can_call(self) -> str | None:
        """None if the brain may be called again; otherwise the reason it may not."""
        if self.calls_left <= 0:
            return "calls"
        if self.tokens_left <= 0:
            return "tokens"
        if self.clock() >= self.deadline:
            return "time"
        return None

    def charge(self, tokens: int = 0) -> None:
        self.calls_left -= 1
        self.calls_used += 1
        tokens = max(0, int(tokens or 0))
        self.tokens_left -= tokens
        self.tokens_used += tokens

    def can_write(self) -> bool:
        return self.written_today < self.daily_writes

    def wrote(self) -> None:
        self.written_today += 1

    def used(self) -> dict:
        return {"brain_calls": self.calls_used, "tokens": self.tokens_used, "written_today": self.written_today}
