"""The local brain (#340): any model the fleet already runs, through the OpenAI-compatible chat API.

One brain for every server that speaks ``POST /v1/chat/completions``: Ollama,
llama.cpp's ``llama-server``, vLLM, and other OpenAI-compatible servers (e.g. a
board's own model server, once confirmed). So a member's mind can think with
the model that member already uses, on the same machine, with nothing sent off
it.

Each call sends the same prompt templates as the Claude Code brain
(``ltms_mind/prompts/``) as one user message, asks for JSON
(``response_format: {"type": "json_object"}``; if the server rejects that field,
once more without it, remembered for this brain), and parses the first JSON
object in the reply. Small models often think aloud first: a ``<think>…</think>``
block and any chatter around the JSON are ignored. An unusable reply gets one
retry that says what was wrong; then the task is dropped (``brain_errors``).

Tokens charged are the server's ``usage.prompt_tokens + completion_tokens``.
Without ``usage``, they're estimated (characters / 4) and ``tokens_estimated``
is set, which the loop copies into the cycle record.

The brain only reasons: no tools, no files, no network beyond its own model
server. ``MIND_LOCAL_API_KEY`` is sent only if set, and never logged.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ltms_mind import prompts
from ltms_mind.brains.base import Brain, Context, Proposal, Task
from ltms_mind.brains.claude_code import BrainError, parse_json_object, proposals_from

log = logging.getLogger("ltms_mind.brains.local")

THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Drop ``<think>…</think>`` blocks (and an unclosed one at the start) from a reply."""
    text = THINK.sub("", text or "")
    if text.lstrip().lower().startswith("<think>"):  # cut off mid-thought: nothing after it is usable
        return ""
    return text


class LocalBrain(Brain):
    name = "local"
    can_dream = True

    def __init__(self, *, url: str, model: str, timeout: float = 120.0, max_tokens: int = 1024,
                 temperature: float = 0.2, api_key: str | None = None,
                 client: httpx.Client | None = None) -> None:
        if not url or not model:
            raise ValueError("the local brain needs MIND_LOCAL_URL and MIND_LOCAL_MODEL")
        self.url = url.rstrip("/") + "/chat/completions"
        self.model, self.timeout, self.max_tokens, self.temperature = model, timeout, max_tokens, temperature
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout)
        self.json_mode = True          # False once the server has rejected response_format
        self.last_tokens = 0
        self.tokens_estimated = False

    def __repr__(self) -> str:  # never shows the key
        return f"LocalBrain(url={self.url!r}, model={self.model!r})"

    # ------------------------------------------------------------------ one call
    def _body(self, prompt: str) -> dict[str, Any]:
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature, "max_tokens": self.max_tokens, "stream": False}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, prompt: str) -> httpx.Response:
        try:
            return self._client.post(self.url, json=self._body(prompt), headers=self._headers, timeout=self.timeout)
        except httpx.TimeoutException:
            raise BrainError(f"the model server timed out after {self.timeout:g}s") from None
        except httpx.HTTPError as exc:
            raise BrainError(f"the model server at {self.url} can't be reached: {type(exc).__name__}") from None

    def run(self, prompt: str) -> str:
        """One chat call. Returns the reply's text; adds its tokens to ``last_tokens``."""
        resp = self._post(prompt)
        if resp.status_code in (400, 422) and self.json_mode and "response_format" in resp.text:
            log.info("the model server rejected response_format; asking without it from now on")
            self.json_mode = False
            resp = self._post(prompt)
        if resp.status_code != 200:
            raise BrainError(f"the model server answered {resp.status_code}: {resp.text.strip()[:200]}")
        try:
            out = resp.json()
            text = out["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise BrainError("the model server's reply isn't an OpenAI-style chat completion") from None
        usage = out.get("usage") or {}
        if "prompt_tokens" in usage or "completion_tokens" in usage:
            self.last_tokens += int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        else:
            self.last_tokens += (len(prompt) + len(text)) // 4
            self.tokens_estimated = True
        return strip_thinking(text)

    def _ask(self, prompt: str, parse):
        """Run, parse; on an unusable reply, one retry that says what was wrong."""
        self.last_tokens = 0
        try:
            return parse(parse_json_object(self.run(prompt)))
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
