"""tool_guard: a Hermes Agent plugin that refuses tool calls whose numbers exceed set limits.

A model can send any number to a tool. For a robot, ``vector_drive`` with
``distance_mm: 2000`` drives two metres; VectorClaw passes it straight to the
robot. This plugin checks each call in Hermes's ``pre_tool_call`` hook, before
the tool runs, and blocks a call whose argument is out of range. Hermes then
returns the block message to the model as the tool's result.

Hermes's contract (v0.23.3): ``pre_tool_call`` gets ``tool_name``, ``args``,
``session_id``, ``task_id``, ``tool_call_id``; returning
``{"action": "block", "message": ...}`` vetoes the call, and any plugin's
block wins over an approval.

**The bridge (#484).** When tool search is on (for example with ``-t mcp-<server>``),
Hermes rewrites a call to a deferred tool into its ``tool_call`` bridge *before*
the hooks run (``agent/turn_tool_validation.py``: ``_route_deferred_call``), so the
hook sees ``tool_name="tool_call"`` and
``args={"calls": [{"name": <real tool>, "arguments": {...}}]}``. The guard
unwraps it the way Hermes's own ``tools/tool_labels.py`` does, checks every inner
call, and blocks the whole bridge call if any inner call to a guarded tool is out
of range or has arguments it can't read.

Pure Python, no dependencies. Never raises: on any internal error the call is
blocked (fail closed) only for tools that have limits; other tools are never
touched.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

log = logging.getLogger("tool_guard")

PLUGIN_VERSION = "0.2.0"
BRIDGE_TOOL = "tool_call"

# Defaults: VectorClaw's drive tool, which has no limits of its own.
DEFAULT_LIMITS: dict[str, dict[str, float]] = {
    "vector_drive": {"distance_mm": 100, "angle_deg": 90, "speed": 100},
}


def resolve_limits(raw: Any) -> dict[str, dict[str, float]]:
    """``{tool: {argument: max_abs}}`` from the settings; bad entries are skipped.

    A setting replaces the defaults entirely, so a deployment can loosen or drop
    them. An empty ``{}`` turns the guard off.
    """
    if not isinstance(raw, dict):
        return {t: dict(a) for t, a in DEFAULT_LIMITS.items()}
    out: dict[str, dict[str, float]] = {}
    for tool, args in raw.items():
        if not (isinstance(tool, str) and tool and isinstance(args, dict)):
            log.warning("tool_guard: ignoring limits entry %r (needs {argument: number})", tool)
            continue
        good = {}
        for arg, limit in args.items():
            if isinstance(arg, str) and isinstance(limit, (int, float)) and not isinstance(limit, bool) \
                    and limit >= 0 and math.isfinite(limit):
                good[arg] = float(limit)
            else:
                log.warning("tool_guard: ignoring limit %s.%s=%r (needs a number >= 0)", tool, arg, limit)
        if good:
            out[tool] = good
    return out


def limits_for(tool_name: str, limits: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """The limits for a tool: an exact name, or the bare name after an MCP prefix.

    ``vector_drive`` matches ``vector_drive`` and ``mcp__<server>__vector_drive``
    for any server, so a second server entry for the same tools is covered too.
    """
    if tool_name in limits:
        return limits[tool_name]
    bare = tool_name.rsplit("__", 1)[-1] if tool_name.startswith("mcp__") else None
    return limits.get(bare) if bare else None


def _as_dict(value: Any) -> Any:
    """Arguments as a dict when they arrive as JSON text; anything else unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value.strip() or "{}")
        except ValueError:
            return value
    return {} if value is None else value


def calls_in(tool_name: str, args: Any) -> list[tuple[str, Any]]:
    """The ``(tool name, arguments)`` pairs a hook call really runs.

    A plain call is itself. A ``tool_call`` bridge call holds its real calls in
    ``args["calls"]`` (or, in the single-call form, is ``{"name", "arguments"}``
    itself), the same reading as Hermes's ``tools/tool_labels.py``.
    """
    args = _as_dict(args)
    if tool_name != BRIDGE_TOOL:
        return [(tool_name, args)]
    if not isinstance(args, dict):
        return [(BRIDGE_TOOL, args)]
    entries = args.get("calls")
    entries = entries if isinstance(entries, list) else [args]
    out = []
    for entry in entries:
        entry = entry if isinstance(entry, dict) else {}
        out.append((str(entry.get("name") or ""), _as_dict(entry.get("arguments"))))
    return out


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def check(tool_name: str, args: Any, limits: dict[str, dict[str, float]]) -> str | None:
    """The reason to refuse this call, or None to let it run."""
    rule = limits_for(str(tool_name or ""), limits)
    if not rule:
        return None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return "its arguments are not an object"
    for arg, limit in rule.items():
        if arg not in args or args[arg] is None:
            continue
        value = args[arg]
        if isinstance(value, bool):
            return f"{arg}={value!r} is not a number"
        if not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return f"{arg}={value!r} is not a number"
        if not math.isfinite(value):
            return f"{arg}={value!r} is not a finite number"
        if abs(value) > limit:
            return (f"{arg}={_fmt(value)} is outside the limit of ±{_fmt(limit)}; "
                    f"use a value between -{_fmt(limit)} and {_fmt(limit)}")
    return None


class Guard:
    def __init__(self, limits: dict[str, dict[str, float]]):
        self.limits = limits
        self.blocked = 0

    def on_pre_tool_call(self, tool_name: Any = None, args: Any = None, **_: Any) -> dict | None:
        name = str(tool_name or "")
        bridged = name == BRIDGE_TOOL
        reason = target = None
        try:
            for inner, inner_args in calls_in(name, args):
                if bridged and inner == BRIDGE_TOOL:
                    # A bridge call whose arguments can't be read could name a guarded
                    # tool, and we can't tell: refuse it (fail closed). Hermes couldn't
                    # run it either.
                    reason, target = "the tool_call arguments could not be read", BRIDGE_TOOL
                    break
                if not limits_for(inner, self.limits):
                    continue
                why = check(inner, inner_args, self.limits)
                if why:
                    reason, target = why, inner
                    break
        except Exception as exc:  # noqa: BLE001 - fail closed, but only for guarded tools
            if bridged or limits_for(name, self.limits):
                reason, target = f"tool_guard could not check the call ({type(exc).__name__})", name
        if reason is None:
            return None
        self.blocked += 1
        via = f" (via {BRIDGE_TOOL})" if bridged and target != BRIDGE_TOOL else ""
        log.warning("tool_guard: blocked %s%s: %s", target, via, reason)
        # "Error:" first, so a recorder that reads this as the tool result marks the call failed.
        return {"action": "block",
                "message": f"Error: refused by tool_guard before {target} ran: {reason}. "
                           f"Nothing was run; the robot did not move."}


def register(ctx) -> None:
    """Hermes entry point. Never raises."""
    try:
        try:
            settings = ctx.get_config() or {}
        except Exception:  # noqa: BLE001
            settings = {}
        limits = resolve_limits(settings.get("limits") if isinstance(settings, dict) else None)
        if not limits:
            log.info("tool_guard: no limits set; not guarding anything")
            return
        guard = Guard(limits)
        ctx.register_hook("pre_tool_call", guard.on_pre_tool_call)
        log.info("tool_guard: guarding %s", ", ".join(
            f"{t} ({', '.join(f'{a} ±{_fmt(v)}' for a, v in rule.items())})" for t, rule in limits.items()))
    except Exception as exc:  # noqa: BLE001 - never break Hermes
        log.warning("tool_guard: not started (%s)", type(exc).__name__)
