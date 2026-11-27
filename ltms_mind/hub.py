"""The mind on a hub (#344, part of #338): the no-brain mind inside the hub process.

The hub already relays its swarm's records and signs a few of its own (the
heartbeat, machine health #349, relay health ``cli_agent.hub_relay``). This
adds the mind's duties, with no model, **inside the hub process**, so the hub's
key keeps exactly one writer and one chain (docs/mind.md, "One writer per key").
It's the first host of ``ltms_mind.embed``.

The harness (``cli_agent``) never imports the mind: the hub loads this as an
extension when its settings say so::

    HUB_EXTENSIONS=ltms_mind.hub:extension

Every ``HUB_MIND_SECONDS`` (default 900; ``0`` turns it off) it signs in, reads
the charter when due, introduces itself to its swarm once, acknowledges
directives (followed: with no brain there's no reason to decline) and writes one
``Mind cycle`` event. Machine health stays with ``hub_health``, so the mind's own
health duty is off.

Settings: ``HUB_MIND_SECONDS``, and any ``MIND_*`` setting except
``MIND_BRAIN`` (always ``none`` here). ``MIND_STATE_PATH`` defaults to
``hub-mind.json`` beside the hub's state file.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping

from ltms_mind.embed import EmbeddedMind, LinkedClient, embedded_config, parse_every

__all__ = ["HubMind", "LinkedClient", "extension", "mind_config"]

log = logging.getLogger(__name__)

MIND_SECONDS = 900.0


def mind_config(env: Mapping[str, str], hub_state_path: str):
    """The hub mind's ``MindConfig`` and wake interval, or None when ``HUB_MIND_SECONDS`` is 0."""
    every = parse_every(env, "HUB_MIND_SECONDS", MIND_SECONDS)
    if every <= 0:
        return None
    cfg = embedded_config(env, os.path.dirname(os.path.abspath(hub_state_path)), state_name="hub-mind.json",
                          defaults={"MIND_HEALTH": "off",        # hub_health (#349) reports the machine
                                    "MIND_EMBEDDER": "lexical"},  # no model downloads on the hub
                          force={"MIND_BRAIN": "none"})
    return cfg, every


class HubMind(EmbeddedMind):
    """The no-brain mind on the hub's own key, inside the hub process."""

    label = "Hub: mind"

    def __init__(self, cfg, every: float, client, link, lock: threading.Lock, now: float) -> None:
        from ltms_mind.brains.none import NoBrain
        from ltms_mind.gate import LexicalEmbedder

        super().__init__(cfg, every, client, link.append, lambda: link.head, lock, now,
                         brain=NoBrain(), embedder=LexicalEmbedder())


def extension(ctx) -> HubMind | None:
    """The hub's extension hook (``HUB_EXTENSIONS=ltms_mind.hub:extension``): the hub's mind,
    or None when ``HUB_MIND_SECONDS`` is 0."""
    settings = mind_config(ctx.env, ctx.state_path)
    if settings is None:
        return None
    mind = HubMind(*settings, ctx.client, ctx.link, ctx.lock, ctx.started_at)
    log.info("Hub: mind (no brain) every %ss", int(mind.every))
    return mind
