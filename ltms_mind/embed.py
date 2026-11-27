"""A mind inside a process that already holds the member's key (#340, #344).

One writer per key (docs/mind.md): a member's mind runs **inside** the process
that already signs for it (the hub, the robot sidecar, the member process, a
Hermes plugin), not beside it, and thinks with the model that member already
uses. This module is what such a host needs:

- ``LinkedClient``: reads through the host's ``LTMSClient``; ``append`` signs
  into the host's own chain; the host's outbox delivers.
- ``EmbeddedMind``: ``tick(now)`` wakes the mind when due, with the member's
  brain (any of ``ltms_mind.brains``), never raising into the host.
- ``embedded_config``: a ``MindConfig`` from the host's settings, with the
  mind's state kept beside the host's.

A host gives it four things: its client (reads), a function that signs into its
chain, its chain head, and the lock it holds while signing.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping

log = logging.getLogger(__name__)


class LinkedClient:
    """The mind's client inside a process that already signs for this key (#344).

    Reads go to ``client`` (an ``LTMSClient`` for the same key). ``append`` signs
    into the host's chain instead of the client's own; the host's outbox delivers
    it, so the mind keeps no outbox of its own and ``flush`` does nothing.
    """

    def __init__(self, client, append: Callable[..., dict], head: Callable[[], dict]) -> None:
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_sign", append)
        object.__setattr__(self, "_head", head)
        object.__setattr__(self, "outbox", [])

    def __getattr__(self, name):
        return getattr(self._client, name)

    def __setattr__(self, name, value) -> None:
        if name == "outbox":  # the mind restores its saved outbox here; the host's outbox has it all
            object.__setattr__(self, name, [])
        else:
            setattr(self._client, name, value)  # e.g. auto_read_charter: the client must see it

    @property
    def seq(self) -> int:
        return self._head()["seq"]

    @property
    def last_hash(self) -> str:
        return self._head()["hash"]

    def append(self, type: str, **kw) -> dict:
        return self._sign(type, **kw)

    def flush(self, batch_size: int = 100) -> list[dict]:
        return []


def embedded_config(env: Mapping[str, str], state_dir: str, *, state_name: str = "mind.json",
                    defaults: Mapping[str, str] | None = None, force: Mapping[str, str] | None = None):
    """A ``MindConfig`` for a mind inside a host: ``MIND_STATE_PATH`` defaults to
    ``<state_dir>/<state_name>``; ``defaults`` apply unless set; ``force`` always wins."""
    from ltms_mind import config

    e = {**(defaults or {}), **dict(env)}
    e.setdefault("MIND_STATE_PATH", os.path.join(os.path.abspath(state_dir), state_name))
    e.update(force or {})
    try:
        return config.load(e)
    except config.ConfigError as exc:
        raise ValueError(str(exc)) from None


def parse_every(env: Mapping[str, str], setting: str, default: float) -> float:
    """Seconds between wakes from ``setting`` (``0`` = off), accepting ``900`` or ``15m``."""
    from ltms_mind import config

    raw = str(env.get(setting, "") or "").strip()
    if not raw:
        return default
    try:
        return config.parse_duration(raw)
    except (ValueError, config.ConfigError):
        raise ValueError(f"{setting} must be a number of seconds or a duration like 15m") from None


class EmbeddedMind:
    """Wakes a member's mind inside its host process, every ``every`` seconds."""

    label = "mind"

    def __init__(self, cfg, every: float, client, sign: Callable[..., dict], head: Callable[[], dict],
                 lock: threading.Lock, now: float, *, brain=None, embedder=None) -> None:
        from ltms_mind import brains
        from ltms_mind.gate import make_embedder

        self.cfg, self.every = cfg, every
        self.next = now  # the first wake comes straight away: introduction and charter
        self.brain = brain if brain is not None else brains.make(cfg.brain, cfg)
        self.embedder = embedder if embedder is not None else make_embedder(cfg.embedder)

        def locked_sign(type: str, **kw) -> dict:
            with lock:
                return sign(type, **kw)

        self.client = LinkedClient(client, locked_sign, head)

    def due(self, now: float) -> bool:
        return now >= self.next

    def tick(self, now: float) -> dict | None:
        """Called by the host on its own schedule: wakes when due. Never raises."""
        if not self.due(now):
            return None
        counts = self.wake(now)
        if counts is not None:
            log.info("%s wake (%s): %s", self.label, self.brain.name,
                     {k: v for k, v in counts.items() if k != "budget"})
        return counts

    def wake(self, now: float) -> dict | None:
        from ltms_mind.loop import Mind

        self.next = now + self.every
        try:
            return Mind(self.cfg, self.client, self.brain, embedder=self.embedder).wake()
        except Exception:  # the mind must never stop its host
            log.exception("%s wake failed", self.label)
            return None
