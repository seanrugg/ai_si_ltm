"""What an adapter gives the sidecar (#312, #408).

An adapter reads one source (a robot SDK, a bridge's log) and calls ``emit``
with exactly one ``Item`` per thing that happened. It never summarises,
filters for meaning or invents: if the source didn't say it, there's no Item.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

RECORD_TYPES = ("perception", "event", "communication")
KINDS = ("record", "body", "sample", "health", "flicker")


@dataclass
class Item:
    """One record to sign, or one sample for the sidecar's window.

    ``kind``:

    - ``record``: one record (``type``, ``summary``, ``tags``, ``payload``).
      ``key`` names it so a later item can refer to it; ``refs`` maps a ref
      name to such a key (``{"start": "ep:held:3"}``), and the sidecar turns it
      into ``"<agent>:<seq>"`` when it signs. A key it hasn't signed is dropped.
      ``at`` (epoch seconds) is when it happened, if not when it was emitted.
    - ``body``: flat ``{name: value}``; pose values (``pose_*``) become the
      sensed pose record (#407), the rest a sensed ``body`` group.
    - ``sample``: one reading of one sensor group (``sensor`` names it:
      ``{"sensor", "modality", "unit"}``), ``values`` ``{channel: value}``;
      the sidecar signs one sensed record per group per window (#408).
      ``sensor`` may carry ``"deadband": {channel: number}``: a per-channel
      change threshold. A channel missing from it (or any channel of an adapter
      that gives none) uses band 0, so any change signs. The sidecar keeps the
      deadband out of the signed record.
    - ``health``: the body reporting on itself (battery, charging); folded
      into one ``internal`` self-report per window (min/max/last).
    - ``flicker``: ``{state: n}`` flag flips too short to be an episode,
      counted in the next window's ``stats.flicker``.
    """
    kind: str
    type: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    payload: dict | None = None
    peers: list[str] | None = None
    values: dict | None = None
    source: str = ""
    sensor: dict | None = None
    key: str | None = None
    refs: dict[str, str] | None = None
    at: float | None = None

    def __post_init__(self):
        if self.kind == "record":
            if self.type not in RECORD_TYPES:
                raise ValueError(f"an adapter record is one of {RECORD_TYPES}, not {self.type!r}")
            if not self.summary:
                raise ValueError("a record needs a summary")
        elif self.kind in KINDS[1:]:
            if not self.values:
                raise ValueError(f"a {self.kind} item needs values")
            if self.kind == "sample" and not (self.sensor or {}).get("sensor"):
                raise ValueError("a sample needs sensor={'sensor': name, ...}")
        else:
            raise ValueError(f"unknown item kind {self.kind!r}")


Emit = Callable[[Item], None]


class Adapter:
    """Override ``start``: begin reading and call ``emit`` for each item. ``stop`` ends it."""

    name = "adapter"

    # Per-channel change thresholds for the health self-report, channel -> band.
    # Adapters that report health override it; the sidecar merges every adapter's
    # dict into its own health_deadband. A channel missing from it uses band 0.
    health_deadband: dict = {}

    def start(self, emit: Emit) -> None:
        raise NotImplementedError

    def resting(self) -> bool:
        """True while the body is at rest (e.g. docked); the sidecar then uses the rest heartbeat.

        The sidecar never knows what "resting" means for a given robot (#449,
        AGENTS.md rule 10); each adapter decides. Default: never resting.
        """
        return False

    def stop(self) -> None:
        pass
