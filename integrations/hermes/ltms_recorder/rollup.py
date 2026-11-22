"""Daily usage rollup for a Hermes agent (#470).

Bounded counters for one UTC day, sent once as a single LTMS record:

* type ``perception``, tags ``telemetry,internal,agent-rollup``
* payload ``{"rollup_schema": 1, "date": ..., sessions, tool_calls, tool_errors,
  model_routes, api_errors, compactions, machine}``

What a rollup never holds: prompts, messages, tool arguments, tool results,
file paths, setting values, error text, URLs. Hook arguments that could carry
any of those are only *classified* (error reason, status class) or reduced to
a name from a known set; the original values are never stored or logged.

The day's counters are saved to ``rollup-<date>.json`` after every update, so a
restart keeps the day. A past day's file is signed into the recorder's chain at
the next hook (local work only, never the network from a hook), sent by the
recorder like any other record, and deleted once LTMS has accepted it.

Plain Python with no Hermes imports. ``__init__.py`` wires it to the hooks.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("ltms_recorder")

ROLLUP_SCHEMA = 1
ROLLUP_TAGS = ["telemetry", "internal", "agent-rollup"]
OTHER = "other"
MAX_TOOL_KEYS = 64
MAX_ROUTES = 16
MAX_NAME = 64
TOOL_ERROR_REASONS = ("timeout", "not_found", "invalid_args", "refused", "unavailable", "upstream_error", "other")
API_ERROR_CLASSES = ("4xx", "5xx", "network", "other")

# Hermes built-in tool names counted by name. Any other non-MCP name is counted
# as "other": a model can invent a tool name, and an invented name is content.
BUILTIN_TOOLS = frozenset({
    "terminal", "process", "read_file", "write_file", "patch", "search_files",
    "web_search", "web_extract", "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_scroll", "browser_back", "browser_press", "browser_close",
    "browser_get_images", "browser_vision", "vision_analyze", "image_generate",
    "text_to_speech", "execute_code", "delegate_task", "mixture_of_agents", "memory",
    "session_search", "todo", "clarify", "skills_list", "skill_view", "skill_manage",
    "send_message", "cronjob",
})
_MCP_NAME = re.compile(r"^mcp__[A-Za-z0-9_-]{1,59}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$")
_FILE = re.compile(r"^rollup-(\d{4}-\d{2}-\d{2})\.json$")

# Error reason from the result's shape. Keywords are matched, never stored.
_REASON_WORDS = (
    ("timeout", ("timed out", "timeout", "deadline exceeded")),
    ("not_found", ("not found", "no such", "does not exist", "unknown tool", "404")),
    ("invalid_args", ("invalid", "missing required", "required argument", "validation", "bad request",
                      "unexpected keyword", "400", "422")),
    ("refused", ("permission denied", "forbidden", "not allowed", "refused", "denied", "unauthorized",
                 "401", "403", "blocked")),
    ("unavailable", ("unavailable", "unreachable", "connection", "not connected", "offline", "503")),
    ("upstream_error", ("internal server error", "server error", "upstream", "bad gateway", "500", "502", "504")),
)


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def empty_counters() -> dict[str, Any]:
    return {
        "sessions": {"started": 0, "completed": 0, "interrupted": 0},
        "tool_calls": {},
        "tool_errors": {},
        "model_routes": {},
        "api_errors": {},
        "compactions": 0,
        "machine": None,
    }


def tool_key(name: Any, known: dict) -> str:
    """The counter key for a tool name: the name if it is a known kind of name, else "other"."""
    name = str(name or "")
    if not (_MCP_NAME.match(name) or name in BUILTIN_TOOLS):
        return OTHER
    if name in known or len([k for k in known if k != OTHER]) < MAX_TOOL_KEYS:
        return name
    return OTHER


def _error_text(result: Any) -> str:
    """Lower-case text to classify. Only used for keyword checks, never kept."""
    obj = result
    if isinstance(result, str):
        try:
            obj = json.loads(result)
        except (ValueError, TypeError):
            return result[:2000].lower()
    if isinstance(obj, dict):
        parts = []
        for key in ("error", "message", "detail", "status", "status_code", "code", "reason"):
            val = obj.get(key)
            if isinstance(val, dict):
                val = " ".join(str(v) for v in val.values())
            if val is not None and not isinstance(val, bool):
                parts.append(str(val))
        return " ".join(parts)[:2000].lower()
    return str(obj)[:2000].lower()


def error_reason(result: Any) -> str:
    """One of TOOL_ERROR_REASONS for a tool result that reported an error."""
    try:
        text = _error_text(result)
    except Exception:  # noqa: BLE001
        return OTHER
    for reason, words in _REASON_WORDS:
        if any(w in text for w in words):
            return reason
    return OTHER


def api_error_class(status_code: Any) -> str:
    if status_code is None:
        return "network"
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return OTHER
    if 400 <= code < 500:
        return "4xx"
    if 500 <= code < 600:
        return "5xx"
    return OTHER


def _label(value: Any, fallback: str) -> str:
    """A provider or model name, or the fallback if it looks like anything else (a URL, a path)."""
    text = "" if value is None else str(value).strip()
    if not text or "://" in text or text.startswith(("/", "~", ".")) or "\\" in text:
        return fallback
    return text[:MAX_NAME] if all(_SAFE_LABEL.match(p) for p in text.split("/") if p) else fallback


def route_key(provider: Any, model: Any) -> str:
    return f"{_label(provider, 'custom')}/{_label(model, 'unknown')}"


def _int(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def usage_tokens(usage: Any) -> tuple[int, int]:
    """(input, output) tokens from a usage dict or object, whichever names it uses."""
    def get(name):
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)
    if usage is None:
        return 0, 0
    inp = get("input_tokens") or get("prompt_tokens") or 0
    out = get("output_tokens") or get("completion_tokens") or 0
    return _int(inp), _int(out)


def hermes_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover
        return None
    for dist in ("hermes-agent", "hermes_agent", "hermes"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            return None
    return None


def machine_facts(plugin_version: str) -> dict[str, Any]:
    ram_gb = None
    try:
        ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
    except (ValueError, OSError, AttributeError):
        pass
    return {"arch": platform.machine() or None, "os": platform.system().lower() or None,
            "ram_gb": ram_gb, "hermes_version": hermes_version(), "plugin_version": plugin_version}


def rollup_summary(date: str, counters: dict) -> str:
    calls = sum(v.get("ok", 0) + v.get("error", 0) for v in counters["tool_calls"].values())
    errors = sum(v.get("error", 0) for v in counters["tool_calls"].values())
    return f"daily rollup {date}: {counters['sessions']['started']} sessions, {calls} tool calls, {errors} errors"


def rollup_payload(date: str, counters: dict) -> dict:
    return {"rollup_schema": ROLLUP_SCHEMA, "date": date, **counters}


class Rollup:
    """Counts one UTC day of Hermes activity and hands each finished day to the recorder."""

    def __init__(self, data_dir: str | Path, *, plugin_version: str,
                 today: Callable[[], str] = utc_today) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.plugin_version = plugin_version
        self._today = today
        self._lock = threading.Lock()       # the current day's counters and file
        self._emit_lock = threading.Lock()  # past days' files
        self.day = today()
        self.counters = self._load(self.day) or empty_counters()
        self._backlog = bool(self._past_days())

    # -------------------------------------------------------------- files
    def path(self, day: str) -> Path:
        return self.dir / f"rollup-{day}.json"

    def _load(self, day: str) -> dict | None:
        try:
            data = json.loads(self.path(day).read_text())
        except (OSError, ValueError):
            return None
        counters = empty_counters()
        for key in counters:
            if key in data:
                counters[key] = data[key]
        return counters

    def _write(self, day: str, data: dict) -> None:
        tmp = self.path(day).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True))
        os.replace(tmp, self.path(day))

    def _past_days(self) -> list[str]:
        today = self._today()
        days = []
        for p in self.dir.iterdir():
            m = _FILE.match(p.name)
            if m and m.group(1) < today:
                days.append(m.group(1))
        return sorted(days)

    # -------------------------------------------------------------- counting
    def _update(self, fn: Callable[[dict], None]) -> None:
        """Apply one change to today's counters and save. Never raises."""
        try:
            with self._lock:
                today = self._today()
                if today != self.day:
                    self.day, self.counters = today, self._load(today) or empty_counters()
                    self._backlog = True
                if self.counters["machine"] is None:
                    self.counters["machine"] = machine_facts(self.plugin_version)
                fn(self.counters)
                self._write(self.day, rollup_payload(self.day, self.counters))
        except Exception as exc:  # noqa: BLE001 - never break Hermes
            log.warning("ltms_recorder: rollup not updated (%s)", type(exc).__name__)

    def on_session_start(self, **_: Any) -> None:
        def f(c):
            c["sessions"]["started"] += 1
        self._update(f)

    def on_session_end(self, completed: Any = None, interrupted: Any = None, **_: Any) -> None:
        def f(c):
            if interrupted:
                c["sessions"]["interrupted"] += 1
            elif completed:
                c["sessions"]["completed"] += 1
        self._update(f)

    def on_post_tool_call(self, tool_name: Any = None, result: Any = None, **_: Any) -> None:
        from .recorder import looks_like_error
        failed = looks_like_error(result)
        reason = error_reason(result) if failed else None

        def f(c):
            key = tool_key(tool_name, c["tool_calls"])
            slot = c["tool_calls"].setdefault(key, {"ok": 0, "error": 0})
            slot["error" if failed else "ok"] += 1
            if reason:
                c["tool_errors"][reason] = c["tool_errors"].get(reason, 0) + 1
        self._update(f)

    def on_post_api_request(self, provider: Any = None, model: Any = None, usage: Any = None,
                            api_duration: Any = None, **_: Any) -> None:
        inp, out = usage_tokens(usage)
        try:
            seconds = max(0.0, float(api_duration or 0))
        except (TypeError, ValueError):
            seconds = 0.0

        def f(c):
            key = route_key(provider, model)
            if key not in c["model_routes"] and len(c["model_routes"]) >= MAX_ROUTES:
                key = OTHER
            r = c["model_routes"].setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                   "api_seconds": 0.0})
            r["calls"] += 1
            r["input_tokens"] += inp
            r["output_tokens"] += out
            r["api_seconds"] = round(r["api_seconds"] + seconds, 3)
        self._update(f)

    def on_api_request_error(self, status_code: Any = None, **_: Any) -> None:
        cls = api_error_class(status_code)

        def f(c):
            c["api_errors"][cls] = c["api_errors"].get(cls, 0) + 1
        self._update(f)

    def on_post_compaction(self, **_: Any) -> None:
        def f(c):
            c["compactions"] += 1
        self._update(f)

    # -------------------------------------------------------------- sending
    def emit_due(self, recorder, *, allow_network: bool = False) -> int:
        """Sign each finished day into the recorder's chain, and delete days LTMS accepted.

        Returns how many rollups were signed. Called from hooks with
        ``allow_network=False`` (local work only); the recorder's flush sends.
        Never raises.
        """
        if not self._backlog:
            return 0
        signed = 0
        try:
            with self._emit_lock:
                days = self._past_days()
                if not days:
                    self._backlog = False
                    return 0
                for day in days:
                    try:
                        data = json.loads(self.path(day).read_text())
                    except (OSError, ValueError):
                        log.warning("ltms_recorder: unreadable rollup file for %s set aside", day)
                        os.replace(self.path(day), self.path(day).with_suffix(".bad"))
                        continue
                    emitted = data.get("_emitted")
                    if emitted:
                        if self._settled(recorder, emitted):
                            self.path(day).unlink(missing_ok=True)
                        continue
                    seq = self._sign(recorder, day, data, allow_network)
                    if seq is None:
                        break  # chain not ready: try again at the next hook
                    data["_emitted"] = {"agent": recorder.client.name, "seq": seq}
                    self._write(day, data)
                    signed += 1
        except Exception as exc:  # noqa: BLE001 - never break Hermes
            log.warning("ltms_recorder: rollup not sent (%s)", type(exc).__name__)
        if signed and getattr(recorder, "_thread", None) is not None:
            recorder._wake.set()
        return signed

    @staticmethod
    def _sign(recorder, day: str, data: dict, allow_network: bool) -> int | None:
        # Sign events already queued first, so the chain stays in time order.
        recorder.sign_pending(allow_network=allow_network)
        counters = empty_counters()
        for key in counters:
            if key in data:
                counters[key] = data[key]
        with recorder._send_lock:
            if not recorder._try_start(allow_network):
                return None
            rec = recorder.client.append("perception", payload=rollup_payload(day, counters),
                                         summary=rollup_summary(day, counters), tags=list(ROLLUP_TAGS))
            if recorder.outbox is not None:
                recorder.outbox.add(rec)
            return rec["seq"]

    @staticmethod
    def _settled(recorder, emitted: dict) -> bool:
        """True once the signed rollup has left the outbox: accepted, or rejected by LTMS (logged there)."""
        with recorder._send_lock:
            if not recorder.ready or emitted.get("agent") != recorder.client.name:
                return False
            seq = emitted.get("seq")
            return not any(r.get("seq") == seq for r in recorder.client.outbox)

    def close(self, recorder) -> None:
        """At exit: sign finished days (network allowed); the recorder's close then sends them."""
        self.emit_due(recorder, allow_network=True)
