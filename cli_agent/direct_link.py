"""Direct mode (#297): a member sends its own outbox to LTMS over HTTPS.

MQTT through the swarm's hub is the primary path. Direct mode is the fallback
when the hub is unreachable, and the normal path for a member without a hub.
Both paths drain the **same** outbox (one chain, one sequence), so switching
never forks the chain. A record that reaches LTMS by both paths is a harmless
duplicate: the server answers ``duplicate`` and keeps one copy.
"""
from __future__ import annotations

import logging

import httpx

from cli_agent.client import LTMSError
from cli_agent.outbox import Outbox


class DirectLink:
    def __init__(self, client, outbox: Outbox, name: str) -> None:
        self.client = client
        self.outbox = outbox
        self.name = name

    def flush(self, batch_size: int = 100) -> int:
        """Send pending records; prune what LTMS acknowledged. Returns how many were pruned. Never raises."""
        pruned = 0
        while True:
            recs = self.outbox.pending(self.name, batch_size)
            if not recs:
                return pruned
            try:
                res = self.client._call("POST", "/api/records", json={"records": recs})
            except (LTMSError, httpx.HTTPError) as exc:
                logging.warning("Direct: send failed, records stay in the outbox: %s", exc)
                return pruned
            head = (res.get("heads") or {}).get(self.name)
            if head is None:
                return pruned
            n = self.outbox.prune(self.name, head["last_ack_seq"])
            pruned += n
            if not res.get("ok", True):
                bad = next((r for r in res.get("results", []) if r.get("status") == "rejected"), {})
                logging.warning("Direct: LTMS rejected %s:%s (%s); stopping", self.name, bad.get("seq"), bad.get("code"))
                return pruned
            if n == 0:
                return pruned  # nothing acknowledged: don't spin


class Transport:
    """Which path to use now, for ``TRANSPORT=mqtt|direct|auto`` (#297).

    ``auto``: MQTT while the broker is connected; after ``fallback_after``
    seconds without a connection, direct. Back to MQTT as soon as it reconnects.
    """

    def __init__(self, mode: str, fallback_after: float, now: float, can_direct: bool = True) -> None:
        self.mode = mode
        self.fallback_after = fallback_after
        self.can_direct = can_direct
        self.connected = False
        self.down_since = now
        self.current = "direct" if mode == "direct" else "mqtt"

    def on_connect(self) -> None:
        self.connected = True

    def on_disconnect(self, now: float) -> None:
        if self.connected:
            self.down_since = now
        self.connected = False

    def choose(self, now: float) -> str:
        if self.mode in ("mqtt", "direct"):
            return self.mode
        before = self.current
        if self.connected:
            self.current = "mqtt"
        elif self.can_direct and now - self.down_since >= self.fallback_after:
            self.current = "direct"
        if self.current != before:
            logging.info("Member: transport %s -> %s", before, self.current)
        return self.current
