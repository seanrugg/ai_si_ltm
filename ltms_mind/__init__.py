"""ltms_mind: the member mind (#338, #339).

A background loop that gives a member a reason to act. It uses the harness
(``cli_agent.LTMSClient``) and never changes it. The loop is the same for every
member; only the brain differs (none, Ollama, Claude Code...). Safety lives in
the loop's code: the brain only proposes, the loop gathers inputs, enforces the
gate and budget, and writes. See ``docs/mind.md``.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
