"""Brains: ``none``, ``claude-code`` (#341) and ``local`` (#340: any OpenAI-compatible model server)."""
from __future__ import annotations

from ltms_mind.brains.base import Brain

BUILT = ("none", "claude-code", "local")


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
    if name in ("local", "ollama"):
        from ltms_mind.brains.local import LocalBrain
        if cfg is None:
            raise ValueError("the local brain needs settings: MIND_LOCAL_URL and MIND_LOCAL_MODEL")
        return LocalBrain(url=cfg.local_url or "", model=cfg.local_model or "", timeout=cfg.local_timeout,
                          max_tokens=cfg.local_max_tokens, api_key=cfg.local_api_key)
    raise ValueError(f"unknown MIND_BRAIN {name!r}: built in: {', '.join(BUILT)}")
