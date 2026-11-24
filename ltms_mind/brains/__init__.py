"""Brains: ``none`` here; Ollama (#340) and Claude Code (#341) plug in the same way."""
from __future__ import annotations

from ltms_mind.brains.base import Brain


def make(name: str) -> Brain:
    if name == "none":
        from ltms_mind.brains.none import NoBrain
        return NoBrain()
    raise ValueError(f"unknown MIND_BRAIN {name!r}: only 'none' is built in so far (#340, #341 add more)")
