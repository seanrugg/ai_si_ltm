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
- ``ThreadedMind`` and ``HostQueue``: for hosts that may only sign from their
  own main thread (the member process, the robot sidecar): the mind thinks on
  its own thread, and the host's loop signs its records (``pump``).

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

    # The host's chain signs without the server (its outbox sends later), so the mind
    # can record an offline or failed wake (#527).
    signs_offline = True

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


class HostQueue:
    """Signs on the host's own thread (#519).

    Some hosts (the member process, the robot sidecar) may only touch their chain
    from their main thread, and a model call can take minutes, so the mind runs
    on its own thread. Its writes go into this queue (``sign`` blocks until
    signed), and the host's loop calls ``pump(link)`` on every pass to sign them
    into its own chain. ``head`` is the chain head as of the last pump.
    """

    def __init__(self, timeout: float = 120.0) -> None:
        import queue
        self._q: queue.Queue = queue.Queue()
        self.timeout = timeout
        from ltms_contract import GENESIS_HASH
        self.head: dict = {"seq": 0, "hash": GENESIS_HASH}  # until the first pump

    def sign(self, type: str, **kw) -> dict:
        from concurrent.futures import Future

        fut: Future = Future()
        self._q.put((type, kw, fut))
        return fut.result(timeout=self.timeout)  # TimeoutError if the host stopped pumping

    def pump(self, link) -> int:
        """On the host's thread: sign everything queued. Returns how many were signed."""
        import queue
        n = 0
        self.head = link.head
        while True:
            try:
                type, kw, fut = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fut.set_result(link.append(type, **kw))
                n += 1
            except Exception as exc:  # noqa: BLE001 - handed back to the mind's thread
                fut.set_exception(exc)
        self.head = link.head
        return n


class ThreadedMind:
    """A member's mind on its own thread, signing through the host's loop (#519).

    - ``brain`` thinks when the mind dreams; ``awake_brain`` (default: the same)
      is used for awake wakes. A robot passes ``NoBrain()`` here, so its model is
      only used while it dreams.
    - ``dream_when()``, if given, decides each wake whether to dream (a robot:
      "on the charger"); otherwise ``MIND_DREAM`` does.
    - The host calls ``pump(link)`` from its own loop, ``start()`` once, ``stop()``
      when it ends.
    """

    def __init__(self, cfg, every: float, client, *, brain=None, awake_brain=None,
                 dream_when: Callable[[], bool] | None = None, embedder=None,
                 clock: Callable[[], float] | None = None, poll_seconds: float = 5.0) -> None:
        import time
        from dataclasses import replace

        from ltms_mind.config import DreamCondition

        self.queue = HostQueue()
        self.clock = clock or time.monotonic
        self.poll_seconds = poll_seconds
        self.dream_when = dream_when
        self._replace, self._Dream = replace, DreamCondition
        self.mind = EmbeddedMind(cfg, every, client, self.queue.sign, lambda: self.queue.head,
                                 _NoLock(), self.clock(), brain=brain, embedder=embedder)
        self.dream_brain = self.mind.brain
        self.awake_brain = awake_brain if awake_brain is not None else self.dream_brain
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def pump(self, link) -> int:
        return self.queue.pump(link)

    def tick(self, now: float) -> dict | None:
        """One check: wake if due, choosing dream or awake (and the brain) for this wake."""
        if not self.mind.due(now):
            return None
        dreaming = None
        if self.dream_when is not None:
            try:
                dreaming = bool(self.dream_when())
            except Exception:  # noqa: BLE001 - unknown: don't dream
                dreaming = False
        base = self.mind.cfg
        if dreaming is not None:
            self.mind.cfg = self._replace(base, dream=self._Dream("always" if dreaming else "never"))
        # Without dream_when the loop decides from MIND_DREAM, so the dreaming brain serves both.
        self.mind.brain = self.awake_brain if dreaming is False else self.dream_brain
        try:
            return self.mind.tick(now)
        finally:
            self.mind.cfg = base

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ltms-mind", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick(self.clock())
            self._stop.wait(self.poll_seconds)


class _NoLock:
    """The host's loop signs one item at a time; nothing else to lock."""

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False
