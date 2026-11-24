"""The brain interface (#339).

A brain turns one agenda task into zero or more **proposals**. It gets text and
data the loop already fetched, and returns proposals. **A brain does no I/O**:
no network, no LTMS calls, no files. The loop decides what is written.

A brain that calls a model reports what the last ``propose`` cost in
``last_tokens`` (the loop charges it to the wake's budget).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# What a proposal is for. The gate's purpose rule passes only these; a proposal
# that only summarises something has no kind here and is dropped (#338).
KINDS = ("lesson", "answer", "review", "prediction", "prediction-check", "question")
VERDICTS = ("verified", "refuted", "unsure")
CHECK_OUTCOMES = ("true", "false", "unclear")

# The record each kind becomes (docs/mind.md, "Record conventions"). No contract change.
RECORD = {
    "lesson": ("lesson", []),
    "answer": ("communication", ["answer"]),
    "review": ("thought", ["review"]),
    "prediction": ("thought", ["prediction"]),
    "prediction-check": ("thought", ["prediction-check"]),
    "question": ("communication", ["question"]),
}


@dataclass
class Task:
    """One agenda item and its inputs, fetched by the loop."""
    category: str            # obligations | verify | predictions | reflect | study
    kind: str                # answer | review | check | reflect | study
    key: str                 # what it is about, e.g. "agent:seq" or "dataset/item"
    text: str                # the task, in words, for a model
    inputs: dict = field(default_factory=dict)  # records, items, observed values


@dataclass
class Context:
    member: str
    swarm: str | None
    family: str | None
    now: str                 # ISO 8601
    mode: str                # awake | dreaming


@dataclass
class Proposal:
    kind: str
    summary: str
    refs: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)   # [{"dataset", "version", "item"}]
    tags: list = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    peers: list = field(default_factory=list)
    rationale: str = ""      # why; for logs only, never written

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Proposal":
        """For brains that parse a model's JSON. Unknown keys are ignored."""
        return cls(kind=str(d.get("kind", "")), summary=str(d.get("summary", "")),
                   refs=dict(d.get("refs") or {}), sources=list(d.get("sources") or []),
                   tags=[str(t) for t in d.get("tags") or []], payload=dict(d.get("payload") or {}),
                   peers=[str(p) for p in d.get("peers") or []], rationale=str(d.get("rationale", "")))


class Brain:
    """Subclass and implement ``propose``."""

    name = "base"
    can_dream = True         # False: the deep (dreaming) tasks never run
    last_tokens = 0

    def propose(self, task: Task, context: Context) -> list[Proposal]:
        raise NotImplementedError
