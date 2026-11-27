"""The Claude Code brain (#341): headless ``claude -p`` reasons, the loop does the rest.

Each call runs::

    claude -p --output-format json --tools "" --strict-mcp-config --no-session-persistence [--model M]

with the prompt on stdin. ``--tools ""`` gives the session **no built-in tools**
and ``--strict-mcp-config`` (with no ``--mcp-config``) **no MCP servers**, so the
model can only reason: it can't read files, run commands or reach the network.
That is the same safety rule as every brain: the loop gathers the inputs, gates
the proposals and writes.

It uses the operator's Claude Code sign-in on the machine. The member's key is
never passed to it: not in the arguments, the prompt or the environment
(``LTMS_*`` and ``MIND_*`` variables are removed), and it never appears in a log.

The process runs in its own empty working directory, so no ``CLAUDE.md`` or
project settings are picked up from wherever the mind was started.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Callable

from ltms_mind import prompts
from ltms_mind.brains.base import Brain, Context, Proposal, Task

log = logging.getLogger("ltms_mind.brains.claude_code")

SCRUB_PREFIXES = ("LTMS_", "MIND_")


class BrainError(RuntimeError):
    pass


def parse_json_object(text: str) -> dict:
    """The first JSON object in a model's reply (code fences and chatter around it are ignored)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("the reply's JSON is not an object")
    return obj


def proposals_from(obj: dict) -> list[Proposal]:
    items = obj.get("proposals")
    if not isinstance(items, list):
        raise ValueError('expected {"proposals": [...]}')
    out = []
    for i, p in enumerate(items):
        if not isinstance(p, dict):
            raise ValueError(f"proposal {i} is not an object")
        out.append(Proposal.from_dict(p))
    return out


class ClaudeCodeBrain(Brain):
    name = "claude-code"
    can_dream = True

    def __init__(self, *, binary: str = "claude", model: str | None = None, timeout: float = 180.0,
                 max_usd: float | None = None, workdir: str | None = None,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 environ: dict | None = None) -> None:
        self.binary, self.model, self.timeout, self.max_usd = binary, model, timeout, max_usd
        self.workdir, self.runner = workdir, runner
        self.environ = os.environ if environ is None else environ
        self.last_tokens = 0
        self.last_cost_usd = 0.0

    # ------------------------------------------------------------------ the subprocess
    def args(self) -> list[str]:
        a = [self.binary, "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
             "--no-session-persistence"]
        if self.model:
            a += ["--model", self.model]
        if self.max_usd:
            a += ["--max-budget-usd", f"{self.max_usd:g}"]
        return a

    def env(self) -> dict:
        return {k: v for k, v in self.environ.items() if not k.startswith(SCRUB_PREFIXES)}

    def _cwd(self) -> str | None:
        if self.workdir:
            os.makedirs(self.workdir, mode=0o700, exist_ok=True)
        return self.workdir

    def run(self, prompt: str) -> str:
        """One headless call. Returns the model's text; adds its tokens to ``last_tokens``."""
        try:
            proc = self.runner(self.args(), input=prompt, capture_output=True, text=True, timeout=self.timeout,
                               env=self.env(), cwd=self._cwd())
        except subprocess.TimeoutExpired:
            raise BrainError(f"claude timed out after {self.timeout:g}s") from None
        except FileNotFoundError:
            raise BrainError(f"claude not found at {self.binary!r} (MIND_CLAUDE_BIN)") from None
        if proc.returncode != 0:
            raise BrainError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:300]}")
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            raise BrainError("claude's output is not JSON (is --output-format json supported?)") from None
        usage = out.get("usage") or {}
        self.last_tokens += sum(int(usage.get(k) or 0) for k in
                                ("input_tokens", "output_tokens", "cache_creation_input_tokens"))
        self.last_cost_usd += float(out.get("total_cost_usd") or 0)
        if out.get("is_error") or out.get("subtype") not in (None, "success"):
            raise BrainError(f"claude reported an error: {str(out.get('result') or out.get('subtype'))[:300]}")
        return str(out.get("result") or "")

    def _ask(self, prompt: str, parse: Callable[[dict], object]):
        """Run, parse; on a bad reply, one retry that says what was wrong."""
        self.last_tokens, self.last_cost_usd = 0, 0.0
        reply = self.run(prompt)
        try:
            return parse(parse_json_object(reply))
        except ValueError as exc:
            log.info("unusable reply (%s); retrying once", exc)
            retry = (f"{prompt}\n\nYour previous reply could not be used: {exc}. "
                     "Answer again with ONE JSON object in the format above and nothing else.")
            try:
                return parse(parse_json_object(self.run(retry)))
            except ValueError as exc2:
                raise BrainError(f"unusable reply after a retry: {exc2}") from None

    # ------------------------------------------------------------------ the brain interface
    def propose(self, task: Task, context: Context) -> list[Proposal]:
        return self._ask(prompts.task_prompt(task, context), proposals_from)

    def decide(self, directive: dict, context: Context) -> tuple[bool, str | None] | None:
        def parse(obj: dict):
            if not isinstance(obj.get("accept"), bool):
                raise ValueError('expected {"accept": true|false, "reason": "..."}')
            return obj["accept"], (str(obj["reason"]) if obj.get("reason") else None)
        return self._ask(prompts.decide_prompt(directive, context), parse)
