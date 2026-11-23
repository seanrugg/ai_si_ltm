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

Pure Python, no dependencies. Never raises: on any internal error the call is
blocked (fail closed) only for tools that have limits; other tools are never
touched.
"""
from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger("tool_guard")

PLUGIN_VERSION = "0.1.0"

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
        rule = None
        try:
            rule = limits_for(str(tool_name or ""), self.limits)
            if not rule:
                return None
            reason = check(tool_name, args, self.limits)
        except Exception as exc:  # noqa: BLE001 - fail closed, but only for guarded tools
            reason = f"tool_guard could not check the call ({type(exc).__name__})" if rule else None
        if reason is None:
            return None
        self.blocked += 1
        log.warning("tool_guard: blocked %s: %s", tool_name, reason)
        # "Error:" first, so a recorder that reads this as the tool result marks the call failed.
        return {"action": "block",
                "message": f"Error: refused by tool_guard before {tool_name} ran: {reason}. "
                           f"The robot did not move."}


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
