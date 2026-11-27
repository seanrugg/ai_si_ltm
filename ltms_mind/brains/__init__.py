"""Brains: ``none`` and ``claude-code`` (#341) here; Ollama (#340) plugs in the same way."""
from __future__ import annotations

from ltms_mind.brains.base import Brain

BUILT = ("none", "claude-code")


def make(name: str, cfg=None) -> Brain:
    """The brain called ``name``; ``cfg`` (a ``MindConfig``) supplies a model brain's settings."""
    if name == "none":
        from ltms_mind.brains.none import NoBrain
        return NoBrain()
    if name == "claude-code":
        from ltms_mind.brains.claude_code import ClaudeCodeBrain
        if cfg is None:
            return ClaudeCodeBrain()
        return ClaudeCodeBrain(binary=cfg.claude_bin, model=cfg.claude_model, timeout=cfg.claude_timeout,
                               max_usd=cfg.claude_max_usd, workdir=cfg.brain_workdir)
    raise ValueError(f"unknown MIND_BRAIN {name!r}: built in so far: {', '.join(BUILT)} (#340 adds ollama)")
