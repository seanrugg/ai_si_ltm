"""Swarm introductions (#334): a member says who it is when it joins a swarm or its
role changes, and may answer another member's introduction once.

State (the swarm ID and role it last introduced itself as) is kept in a small
JSON file beside the member's state, so a restart sends nothing new.
"""
from __future__ import annotations

import json
import os
import random
from typing import Callable

INTRO_TAG = "introduction"
MAX_SUMMARY = 500


def harness_version() -> str:
    try:
        from importlib.metadata import version
        return version("ltms-harness")
    except Exception:  # noqa: BLE001 - not installed as a package
        return "unknown"


def describe(me: dict | None) -> tuple[str, str]:
    """(kind, primary-model text) from this member's roster entry."""
    kind = (me or {}).get("kind") or "member"
    summary = (me or {}).get("summary") or ""
    return kind, summary


def intro_text(name: str, swarm_name: str, me: dict | None, reply: bool = False) -> str:
    kind, summary = describe(me)
    article = "an" if kind[:1].lower() in "aeiou" else "a"
    role = (me or {}).get("role") or "member"
    text = (f"Hi {swarm_name}, {name} here: {article} {kind}, {role} of this swarm" if reply
            else f"Hello {swarm_name}, I'm {name}, {article} {kind} joining as {role}")
    if summary:
        text += f" ({summary})"
    return (text + ".")[:MAX_SUMMARY]


class Introducer:
    """Decides when to introduce and reply. ``send(text, payload, reply_to)`` signs and sends."""

    def __init__(self, name: str, state_path: str, send: Callable, *, reply_policy: str = "reply",
                 rand: Callable[[], float] = random.random, max_delay_s: float = 30.0):
        if reply_policy not in ("reply", "off"):
            raise ValueError("INTRO_REPLY must be reply or off")
        self.name, self.state_path, self.send = name, state_path, send
        self.reply_policy, self.rand, self.max_delay_s = reply_policy, rand, max_delay_s
        self.replied: set[str] = set()
        self.pending: list[tuple[float, str]] = []  # (due, reply_to)
        self.swarm: dict | None = None

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.state_path)

    def _me(self) -> dict | None:
        return next((m for m in (self.swarm or {}).get("roster") or [] if m.get("name") == self.name), None)

    def check(self, swarm: dict | None) -> dict | None:
        """Call with ``GET /api/swarm``'s result. Introduces itself if the swarm or role is new."""
        self.swarm = swarm
        if not swarm:
            return None
        last = self._load()
        if last.get("swarm") == swarm.get("id") and last.get("role") == swarm.get("role"):
            return None
        me = self._me()
        payload = {"swarm": swarm.get("id"), "role": swarm.get("role"),
                   "specs_summary": (me or {}).get("summary") or None,
                   "capabilities": [], "harness_version": harness_version()}
        rec = self.send(intro_text(self.name, swarm.get("name") or "swarm", me), payload, None)
        self._save({"swarm": swarm.get("id"), "role": swarm.get("role")})
        return rec

    def on_message(self, rec: dict, now: float) -> bool:
        """A swarm message arrived. Schedules one reply to another member's (first) introduction."""
        if INTRO_TAG not in (rec.get("tags") or []) or rec.get("agent") == self.name:
            return False
        if (rec.get("refs") or {}).get("reply_to"):
            return False  # never answer a reply: no chains of introductions
        key = f"{rec['agent']}:{rec['seq']}"
        if self.reply_policy == "off" or key in self.replied or not self.swarm:
            return False
        self.replied.add(key)
        self.pending.append((now + self.rand() * self.max_delay_s, key))
        return True

    def due(self, now: float) -> list:
        """Send the replies whose random delay has passed."""
        out, keep = [], []
        for at, key in self.pending:
            if at > now:
                keep.append((at, key))
                continue
            me = self._me()
            payload = {"swarm": (self.swarm or {}).get("id"), "role": (self.swarm or {}).get("role"),
                       "specs_summary": (me or {}).get("summary") or None, "harness_version": harness_version()}
            out.append(self.send(intro_text(self.name, (self.swarm or {}).get("name") or "swarm", me, reply=True),
                                 payload, key))
        self.pending = keep
        return out
