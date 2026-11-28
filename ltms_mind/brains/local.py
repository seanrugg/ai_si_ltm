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

**Context (#529).** Each call's prompt and completion tokens and its
``finish_reason`` are added up for the wake (``take_usage``, the cycle record's
``usage``); ``truncated`` counts replies cut off at ``max_tokens``. The model's
context length comes from ``MIND_LOCAL_CONTEXT`` or, once, from the server:
llama.cpp's ``/props`` (``n_ctx``), or Ollama's ``/api/ps`` (the loaded
context), the Modelfile's ``num_ctx`` (``/api/show``), else the model's trained
``context_length``, which is only a ceiling. Each call's ``max_tokens`` is then
min(``MIND_LOCAL_MAX_TOKENS``, context − estimated prompt − margin), and a prompt
that leaves less than ``MIN_REPLY`` is not sent (``too_long``), because a server
would cut it without an error. Ollama's ``/v1`` can't set ``num_ctx``;
``MIND_LOCAL_API=ollama`` uses its native ``/api/chat``, which sends
``MIND_LOCAL_CONTEXT`` as ``options.num_ctx``.

The brain only reasons: no tools, no files, no network beyond its own model
server. ``MIND_LOCAL_API_KEY`` is sent only if set, and never logged.
"""
from __future__ import annotations

import logging
import math
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
APIS = ("openai", "ollama")
# Context sizing (#529): tokens kept free beyond the estimate (chat template, tokenizer
# drift), the shortest reply worth asking for, and characters per token until the
# server's own counts say otherwise (low, so a prompt is overestimated, not cut).
MARGIN = 64
MIN_REPLY = 128
CHARS_PER_TOKEN = 3.0
USAGE_KEYS = ("calls", "prompt_tokens", "completion_tokens", "truncated", "too_long", "max_prompt")


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
                 temperature: float = 0.2, api_key: str | None = None, api: str = "openai",
                 context: int | None = None, probe: bool = False,
                 client: httpx.Client | None = None) -> None:
        if not url or not model:
            raise ValueError("the local brain needs MIND_LOCAL_URL and MIND_LOCAL_MODEL")
        if api not in APIS:
            raise ValueError(f"MIND_LOCAL_API must be one of {', '.join(APIS)}, not {api!r}")
        base = url.rstrip("/")
        self.root = base.removesuffix("/v1")  # where /props and /api/* live
        self.api = api
        self.url = self.root + "/api/chat" if api == "ollama" else base + "/chat/completions"
        self.model, self.timeout, self.max_tokens, self.temperature = model, timeout, max_tokens, temperature
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout)
        self.format_mode = FORMATS[0]  # steps down when the server rejects it (FORMATS)
        self.last_tokens = 0
        self.tokens_estimated = False
        # Context (#529): set, or read from the server once (``probe``), or unknown.
        self.num_ctx = context if context and context > 0 else None  # sent as options.num_ctx (ollama api)
        self.context, self.context_source = (self.num_ctx, "setting") if self.num_ctx else (None, None)
        self._probe = probe and self.context is None
        self.chars_per_token = CHARS_PER_TOKEN
        self.usage = dict.fromkeys(USAGE_KEYS, 0)

    def __repr__(self) -> str:  # never shows the key
        return f"LocalBrain(url={self.url!r}, model={self.model!r})"

    @property
    def json_mode(self) -> bool:
        """True while the server takes some ``response_format`` (schema or plain JSON)."""
        return self.format_mode is not None

    # ------------------------------------------------------------------ context (#529)
    def _get_json(self, method: str, path: str, **kw) -> dict | None:
        """One probe request; None if it fails or isn't JSON. Raises nothing."""
        try:
            resp = self._client.request(method, self.root + path, headers=self._headers,
                                        timeout=min(self.timeout, 10.0), **kw)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            out = resp.json()
        except ValueError:
            return None
        return out if isinstance(out, dict) else None

    def probe_context(self) -> int | None:
        """Read the model's context length from the server (llama.cpp, then Ollama).
        Sets ``context`` and ``context_source``; leaves them unset if nothing answers."""
        props = self._get_json("GET", "/props")                     # llama.cpp's llama-server
        if props is not None:
            gen = props.get("default_generation_settings") or {}
            n = _int(gen.get("n_ctx")) or _int(props.get("n_ctx"))
            if n:
                return self._set_context(n, "server")
        ps = self._get_json("GET", "/api/ps")                       # Ollama: what's loaded now
        for m in (ps or {}).get("models") or []:
            if self.model in (m.get("name"), m.get("model")) and _int(m.get("context_length")):
                return self._set_context(_int(m["context_length"]), "server")
        show = self._get_json("POST", "/api/show", json={"model": self.model})
        if show is not None:
            found = re.search(r"^\s*num_ctx\s+(\d+)", str(show.get("parameters") or ""), re.MULTILINE)
            if found:
                return self._set_context(int(found.group(1)), "modelfile")
            trained = [_int(v) for k, v in (show.get("model_info") or {}).items() if k.endswith(".context_length")]
            if any(trained):
                return self._set_context(max(trained), "model")   # a ceiling: the server may use less
        return None

    def _set_context(self, n: int, source: str) -> int:
        self.context, self.context_source = n, source
        log.info("model context: %d tokens (%s)", n, source)
        return n

    def reply_budget(self, prompt: str) -> int:
        """The ``max_tokens`` for this prompt: what's left of the context, at most the setting.
        Raises ``BrainError`` (and counts ``too_long``) if too little is left to reply."""
        if self._probe:
            self._probe = False
            self.probe_context()
        if not self.context:
            return self.max_tokens
        est = math.ceil(len(prompt) / self.chars_per_token)
        room = self.context - est - MARGIN
        if room < min(MIN_REPLY, self.max_tokens):
            self.usage["too_long"] += 1
            raise BrainError(f"the prompt (about {est} tokens) doesn't fit the model's context "
                             f"({self.context}, {self.context_source})")
        return min(self.max_tokens, room)

    # ------------------------------------------------------------------ one call
    def _body(self, prompt: str, schema: dict | None = None, max_tokens: int | None = None) -> dict[str, Any]:
        max_tokens = max_tokens or self.max_tokens
        messages = [{"role": "user", "content": prompt}]
        if self.api == "ollama":                                    # native /api/chat
            options = {"temperature": self.temperature, "num_predict": max_tokens}
            if self.num_ctx:
                options["num_ctx"] = self.num_ctx
            body = {"model": self.model, "messages": messages, "stream": False, "options": options}
            if self.format_mode == "schema" and schema is not None:
                body["format"] = schema
            elif self.format_mode is not None:
                body["format"] = "json"
            return body
        body = {"model": self.model, "messages": messages,
                "temperature": self.temperature, "max_tokens": max_tokens, "stream": False}
        if self.format_mode == "schema" and schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "proposals", "schema": schema}}
        elif self.format_mode is not None:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, prompt: str, schema: dict | None = None, max_tokens: int | None = None) -> httpx.Response:
        try:
            return self._client.post(self.url, json=self._body(prompt, schema, max_tokens), headers=self._headers,
                                     timeout=self.timeout)
        except httpx.TimeoutException:
            raise BrainError(f"the model server timed out after {self.timeout:g}s") from None
        except httpx.HTTPError as exc:
            raise BrainError(f"the model server at {self.url} can't be reached: {type(exc).__name__}") from None

    def _rejected_format(self, resp: httpx.Response, schema: dict | None) -> bool:
        """True if this reply refuses the ``response_format`` we sent (not some other 400)."""
        if resp.status_code not in (400, 422) or self.format_mode is None:
            return False
        words = ("response_format", "format") if self.api == "ollama" else ("response_format",)
        if self.format_mode == "schema" and schema is None:  # we sent json_object, not the schema
            return any(w in resp.text for w in words)
        return any(w in resp.text for w in (*words, "json_schema", "schema"))

    def _reply(self, out: dict) -> tuple[str, int | None, int | None, str | None]:
        """(text, prompt tokens, completion tokens, finish reason) from either API's reply."""
        if self.api == "ollama":
            text = out["message"]["content"] or ""
            return text, _int(out.get("prompt_eval_count")), _int(out.get("eval_count")), out.get("done_reason")
        choice = out["choices"][0]
        usage = out.get("usage") or {}
        return (choice["message"]["content"] or "", _int(usage.get("prompt_tokens")),
                _int(usage.get("completion_tokens")), choice.get("finish_reason"))

    def run(self, prompt: str, schema: dict | None = None) -> str:
        """One chat call. Returns the reply's text; adds its tokens to ``last_tokens``
        and to the wake's ``usage``. ``schema`` constrains the reply while the server accepts it."""
        max_tokens = self.reply_budget(prompt)
        resp = self._post(prompt, schema, max_tokens)
        while self._rejected_format(resp, schema):
            if self.format_mode == "schema" and schema is None:
                self.format_mode = "object"  # a rejected json_object goes past the schema step
            nxt = FORMATS[FORMATS.index(self.format_mode) + 1]
            log.info("the model server rejected response_format %s; using %s from now on",
                     self.format_mode, nxt or "none")
            self.format_mode = nxt
            resp = self._post(prompt, schema, max_tokens)
        if resp.status_code != 200:
            raise BrainError(f"the model server answered {resp.status_code}: {resp.text.strip()[:200]}")
        try:
            text, p_tok, c_tok, finish = self._reply(resp.json())
        except (ValueError, KeyError, IndexError, TypeError):
            kind = "an Ollama chat reply" if self.api == "ollama" else "an OpenAI-style chat completion"
            raise BrainError(f"the model server's reply isn't {kind}") from None
        if p_tok is None and c_tok is None:
            p_tok, c_tok = math.ceil(len(prompt) / 4), math.ceil(len(text) / 4)
            self.tokens_estimated = True
        p_tok, c_tok = p_tok or 0, c_tok or 0
        if p_tok > 0 and not self.tokens_estimated:  # the server's count: learn this model's ratio
            self.chars_per_token = min(6.0, max(1.5, len(prompt) / p_tok))
        self.last_tokens += p_tok + c_tok
        u = self.usage
        u["calls"] += 1
        u["prompt_tokens"] += p_tok
        u["completion_tokens"] += c_tok
        u["max_prompt"] = max(u["max_prompt"], p_tok)
        if finish == "length":
            u["truncated"] += 1
        log.info("local call: prompt %d, completion %d of %d, finish %s", p_tok, c_tok, max_tokens, finish)
        return strip_thinking(text)

    def take_usage(self) -> dict:
        """The token counts since the last take (#529), for the cycle record; then resets them.
        Empty if no call was made or refused."""
        u, self.usage = self.usage, dict.fromkeys(USAGE_KEYS, 0)
        if not (u["calls"] or u["too_long"]):
            return {}
        out = {k: v for k, v in u.items() if v or k in ("calls", "prompt_tokens", "completion_tokens")}
        if self.context:
            out["context"], out["context_source"] = self.context, self.context_source
        out["max_tokens"] = self.max_tokens
        return out

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


def _int(v) -> int | None:
    """A positive int from a server's JSON value, else None."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None
