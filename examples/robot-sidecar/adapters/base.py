"""What an adapter gives the sidecar (#312).

An adapter reads one source (a robot SDK, a bridge's log) and calls ``emit``
with exactly one ``Item`` per thing that happened. It never summarises,
filters for meaning or invents: if the source didn't say it, there's no Item.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

RECORD_TYPES = ("perception", "event", "communication")


@dataclass
class Item:
    """One record to sign (``kind="record"``) or one body-state sample (``kind="body"``).

    A body sample is ``values``: flat ``{name: number}``; the sidecar folds
    samples into one ``internal,telemetry`` record per window (min/max/last).
    """
    kind: str
    type: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    payload: dict | None = None
    peers: list[str] | None = None
    values: dict[str, float] | None = None
    source: str = ""

    def __post_init__(self):
        if self.kind == "record":
            if self.type not in RECORD_TYPES:
                raise ValueError(f"an adapter record is one of {RECORD_TYPES}, not {self.type!r}")
            if not self.summary:
                raise ValueError("a record needs a summary")
        elif self.kind == "body":
            if not self.values:
                raise ValueError("a body sample needs values")
        else:
            raise ValueError(f"unknown item kind {self.kind!r}")


Emit = Callable[[Item], None]


class Adapter:
    """Override ``start``: begin reading and call ``emit`` for each item. ``stop`` ends it."""

    name = "adapter"

    def start(self, emit: Emit) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass
