"""Relay health on change for a hub (#344): backlog, MQTT bus, relay to LTMS.

Checked every hub tick. One ``event`` tagged ``internal,health,relay,<check>``
when a check changes state and the new state has held for
``HUB_RELAY_HOLD_SECONDS`` (default 60); nothing while it stays. Each event refs
the previous one for the same check. Machine health (disk, temperature, memory,
throttling) is ``hub_health``'s (#349).

Settings: ``HUB_BACKLOG_MAX`` (default 500 records), ``HUB_RELAY_HOLD_SECONDS``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import ClassVar

log = logging.getLogger(__name__)

BACKLOG_MAX = 500
HOLD_SECONDS = 60.0


class RelayWatch:
    """Relay health on change (#344): backlog, MQTT bus, relay to LTMS.

    ``observe`` takes the current readings and returns the changes that have now
    held for ``hold_s``: ``(check, state, previous, detail)``. ``record`` signs
    each as an ``event`` tagged ``internal,health,relay,<check>``. Starting
    states are the healthy ones, so a hub that starts healthy writes nothing.
    """

    HEALTHY: ClassVar[dict[str, str]] = {"backlog": "ok", "bus": "up", "relay": "ok"}

    def __init__(self, backlog_max: int = BACKLOG_MAX, hold_s: float = HOLD_SECONDS) -> None:
        self.backlog_max, self.hold_s = backlog_max, hold_s
        self.state = dict(self.HEALTHY)
        self.pending: dict[str, tuple[str, float]] = {}  # check -> (new state, since)
        self.last_ref: dict[str, str] = {}               # check -> "<agent>:<seq>" of its last event

    def observe(self, now: float, *, backlog: int | None = None, bus_up: bool | None = None,
                relay_ok: bool | None = None) -> list[tuple[str, str, str, dict]]:
        readings = {}
        if backlog is not None:
            readings["backlog"] = ("high" if backlog > self.backlog_max else "ok",
                                   {"records": backlog, "threshold": self.backlog_max})
        if bus_up is not None:
            readings["bus"] = ("up" if bus_up else "down", {})
        if relay_ok is not None:
            readings["relay"] = ("ok" if relay_ok else "failing", {})
        changes = []
        for check, (state, detail) in readings.items():
            if state == self.state[check]:
                self.pending.pop(check, None)
                continue
            seen = self.pending.get(check)
            if seen is None or seen[0] != state:
                self.pending[check] = (state, now)
                seen = self.pending[check]
            if now - seen[1] >= self.hold_s:
                previous, self.state[check] = self.state[check], state
                del self.pending[check]
                changes.append((check, state, previous, {**detail, "held_s": round(now - seen[1], 1)}))
        return changes

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> RelayWatch:
        try:
            backlog_max = int(env.get("HUB_BACKLOG_MAX", str(BACKLOG_MAX)))
            hold_s = float(env.get("HUB_RELAY_HOLD_SECONDS", str(HOLD_SECONDS)))
        except (TypeError, ValueError):
            raise ValueError("HUB_BACKLOG_MAX must be a whole number and HUB_RELAY_HOLD_SECONDS a number") from None
        if backlog_max <= 0 or hold_s < 0:
            raise ValueError("HUB_BACKLOG_MAX must be > 0 and HUB_RELAY_HOLD_SECONDS >= 0")
        return cls(backlog_max, hold_s)

    def record(self, append: Callable[..., dict], changes) -> int:
        for check, state, previous, detail in changes:
            header = {"summary": f"relay {check}: {state}", "tags": ["internal", "health", "relay", check]}
            if check in self.last_ref:
                header["refs"] = {"previous": self.last_ref[check]}
            rec = append("event", payload={"check": check, "state": state, "previous": previous, **detail},
                         **header)
            if isinstance(rec, dict) and rec.get("agent") and rec.get("seq") is not None:
                self.last_ref[check] = f"{rec['agent']}:{rec['seq']}"
            log.warning("Hub: relay %s %s (was %s)", check, state, previous)
        return len(changes)
