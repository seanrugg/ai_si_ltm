"""The local brain (#340): any model the fleet already runs, through the OpenAI-compatible chat API.

One brain for every server that speaks ``POST /v1/chat/completions``: Ollama,
llama.cpp's ``llama-server``, vLLM, and other OpenAI-compatible servers (e.g. a
board's own model server, once confirmed). So a member's mind can think with
the model that member already uses, on the same machine, with nothing sent off
it.

Each call sends the same prompt templates as the Claude Code brain
(``ltms_mind/prompts/``) as one user message and asks for JSON constrained to
the task (#540): ``response_format: {"type": "json_schema", ...}`` whose ``kind``
is an enum of the kinds the task allows (``ALLOWED_KINDS``) and whose ``refs``
values must be record keys (the gate's ``REF_RE``). Ollama's ``/v1`` and
llama.cpp's server both take it, so a small model can't write the wrong kind or
copy a placeholder key. If the server rejects that, the brain steps down to
``{"type": "json_object"}``, then to no ``response_format`` at all, one step per
rejection, remembered for this brain. It parses the first JSON object in the reply. Small models often think aloud first: a ``<think>…</think>``
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
from ltms_mind.brains.base import ALLOWED_KINDS, Brain, Context, Proposal, Task
from ltms_mind.brains.claude_code import BrainError, parse_json_object, proposals_from

log = logging.getLogger("ltms_mind.brains.local")

THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# How the reply is constrained, strictest first; a rejection moves one step down (#540).
FORMATS = ("schema", "object", None)


def proposals_schema(task_kind: str) -> dict:
    """The JSON schema for a task's reply: only its allowed kinds, refs only as record keys."""
    from ltms_mind.gate import (  # the gate's own rules; imported late (gate imports brains)
        MAX_SUMMARY,
        REF_RE,
    )
    key = {"type": "string", "pattern": REF_RE.pattern}
    proposal = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(ALLOWED_KINDS[task_kind])},
            "summary": {"type": "string", "minLength": 1, "maxLength": MAX_SUMMARY},
            "refs": {"type": "object",
                     "additionalProperties": {"anyOf": [key, {"type": "array", "items": key, "minItems": 1}]}},
            "sources": {"type": "array", "items": {"type": "object"}},
            "payload": {"type": "object"},
            "peers": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "required": ["kind", "summary"],
    }
    return {"type": "object", "properties": {"proposals": {"type": "array", "items": proposal}},
            "required": ["proposals"]}


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
        self.format_mode = FORMATS[0]  # steps down when the server rejects it (FORMATS)
        self.last_tokens = 0
        self.tokens_estimated = False

    def __repr__(self) -> str:  # never shows the key
        return f"LocalBrain(url={self.url!r}, model={self.model!r})"

    @property
    def json_mode(self) -> bool:
        """True while the server takes some ``response_format`` (schema or plain JSON)."""
        return self.format_mode is not None

    # ------------------------------------------------------------------ one call
    def _body(self, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature, "max_tokens": self.max_tokens, "stream": False}
        if self.format_mode == "schema" and schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "proposals", "schema": schema}}
        elif self.format_mode is not None:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, prompt: str, schema: dict | None = None) -> httpx.Response:
        try:
            return self._client.post(self.url, json=self._body(prompt, schema), headers=self._headers,
                                     timeout=self.timeout)
        except httpx.TimeoutException:
            raise BrainError(f"the model server timed out after {self.timeout:g}s") from None
        except httpx.HTTPError as exc:
            raise BrainError(f"the model server at {self.url} can't be reached: {type(exc).__name__}") from None

    def _rejected_format(self, resp: httpx.Response, schema: dict | None) -> bool:
        """True if this reply refuses the ``response_format`` we sent (not some other 400)."""
        if resp.status_code not in (400, 422) or self.format_mode is None:
            return False
        if self.format_mode == "schema" and schema is None:  # we sent json_object, not the schema
            return "response_format" in resp.text
        return any(w in resp.text for w in ("response_format", "json_schema", "schema"))

    def run(self, prompt: str, schema: dict | None = None) -> str:
        """One chat call. Returns the reply's text; adds its tokens to ``last_tokens``.
        ``schema`` constrains the reply while the server accepts it."""
        resp = self._post(prompt, schema)
        while self._rejected_format(resp, schema):
            if self.format_mode == "schema" and schema is None:
                self.format_mode = "object"  # a rejected json_object goes past the schema step
            nxt = FORMATS[FORMATS.index(self.format_mode) + 1]
            log.info("the model server rejected response_format %s; using %s from now on",
                     self.format_mode, nxt or "none")
            self.format_mode = nxt
            resp = self._post(prompt, schema)
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

    def _ask(self, prompt: str, parse, schema: dict | None = None):
        """Run, parse; on an unusable reply, one retry that says what was wrong."""
        self.last_tokens = 0
        try:
            return parse(parse_json_object(self.run(prompt, schema)))
        except ValueError as exc:
            log.info("unusable reply (%s); retrying once", exc)
            retry = (f"{prompt}\n\nYour previous reply could not be used: {exc}. "
                     "Answer again with ONE JSON object in the format above and nothing else.")
            try:
                return parse(parse_json_object(self.run(retry, schema)))
            except ValueError as exc2:
                raise BrainError(f"unusable reply after a retry: {exc2}") from None

    # ------------------------------------------------------------------ the brain interface
    def propose(self, task: Task, context: Context) -> list[Proposal]:
        schema = proposals_schema(task.kind) if task.kind in ALLOWED_KINDS else None
        return self._ask(prompts.task_prompt(task, context), proposals_from, schema)

    def decide(self, directive: dict, context: Context) -> tuple[bool, str | None] | None:
        def parse(obj: dict):
            if not isinstance(obj.get("accept"), bool):
                raise ValueError('expected {"accept": true|false, "reason": "..."}')
            return obj["accept"], (str(obj["reason"]) if obj.get("reason") else None)
        return self._ask(prompts.decide_prompt(directive, context), parse)
