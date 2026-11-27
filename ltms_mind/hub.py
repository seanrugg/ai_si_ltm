"""The mind on a hub (#344, part of #338): the no-brain mind inside the hub process.

The hub already relays its swarm's records and signs a few of its own (the
heartbeat, machine health #349, relay health ``cli_agent.hub_relay``). This
adds the mind's duties, with no model, **inside the hub process**, so the hub's
key keeps exactly one writer and one chain (docs/mind.md, "One writer per key").

The harness (``cli_agent``) never imports the mind: the hub loads this as an
extension when its settings say so::

    HUB_EXTENSIONS=ltms_mind.hub:extension

Every ``HUB_MIND_SECONDS`` (default 900; ``0`` turns it off) it signs in, reads
the charter when due, introduces itself to its swarm once, acknowledges
directives (followed: with no brain there's no reason to decline) and writes one
``Mind cycle`` event. Machine health stays with ``hub_health``, so the mind's own
health duty is off.

Reads use the hub's own ``LTMSClient``; writes are signed into the hub's chain
(``LinkedClient`` over its ``MemberLink``) and the hub's outbox delivers them,
like the heartbeat.

Settings: ``HUB_MIND_SECONDS``, and any ``MIND_*`` setting except
``MIND_BRAIN`` (always ``none`` here). ``MIND_STATE_PATH`` defaults to
``hub-mind.json`` beside the hub's state file.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping

log = logging.getLogger(__name__)

MIND_SECONDS = 900.0


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


def mind_config(env: Mapping[str, str], hub_state_path: str):
    """The hub mind's ``MindConfig`` and wake interval, or None when ``HUB_MIND_SECONDS`` is 0."""
    from ltms_mind import config

    try:
        every = float(env.get("HUB_MIND_SECONDS", str(MIND_SECONDS)))
    except (TypeError, ValueError):
        raise ValueError("HUB_MIND_SECONDS must be a number") from None
    if every <= 0:
        return None
    e = dict(env)
    e.setdefault("MIND_STATE_PATH", os.path.join(os.path.dirname(os.path.abspath(hub_state_path)), "hub-mind.json"))
    e["MIND_BRAIN"] = "none"
    e.setdefault("MIND_HEALTH", "off")        # hub_health (#349) reports the machine
    e.setdefault("MIND_EMBEDDER", "lexical")  # no model downloads on the hub
    try:
        cfg = config.load(e)
    except config.ConfigError as exc:
        raise ValueError(str(exc)) from None
    return cfg, every


class HubMind:
    """Wakes the no-brain mind on the hub's own key, inside the hub process."""

    def __init__(self, cfg, every: float, client, link, lock: threading.Lock, now: float) -> None:
        from ltms_mind.brains.none import NoBrain
        from ltms_mind.gate import LexicalEmbedder

        self.cfg, self.every = cfg, every
        self.next = now  # the first wake comes straight away: introduction and charter
        self.brain, self.embedder = NoBrain(), LexicalEmbedder()

        def sign(type: str, **kw) -> dict:
            with lock:
                return link.append(type, **kw)

        self.client = LinkedClient(client, sign, lambda: link.head)

    def due(self, now: float) -> bool:
        return now >= self.next

    def tick(self, now: float) -> None:
        """Called by the hub every tick: wake when due."""
        if self.due(now):
            counts = self.wake(now)
            if counts is not None:
                log.info("Hub: mind wake: %s", {k: v for k, v in counts.items() if k != "budget"})

    def wake(self, now: float) -> dict | None:
        from ltms_mind.loop import Mind

        self.next = now + self.every
        try:
            return Mind(self.cfg, self.client, self.brain, embedder=self.embedder).wake()
        except Exception:  # the mind must never stop the relay
            log.exception("Hub: mind wake failed")
            return None


def extension(ctx) -> HubMind | None:
    """The hub's extension hook (``HUB_EXTENSIONS=ltms_mind.hub:extension``): the hub's mind,
    or None when ``HUB_MIND_SECONDS`` is 0."""
    settings = mind_config(ctx.env, ctx.state_path)
    if settings is None:
        return None
    mind = HubMind(*settings, ctx.client, ctx.link, ctx.lock, ctx.started_at)
    log.info("Hub: mind (no brain) every %ss", int(mind.every))
    return mind
