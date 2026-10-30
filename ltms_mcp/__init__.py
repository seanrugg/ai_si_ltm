"""ltms_mcp: an MCP server so agent frameworks can join LTMS as members.

The tool logic lives here as plain functions that take an ``LTMSClient`` and
know nothing about MCP. ``ltms_mcp.server`` wraps them with FastMCP. Tests can
call these functions directly with no MCP transport installed.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from cli_agent.client import LTMSClient, LTMSError
from ltms_contract import RECORD_TYPES

# Types a model may write through the MCP server. ``policy`` comes from an
# operator, not a model; ``system`` is reserved for the server.
ALLOWED_TYPES = (
    "communication",
    "thought",
    "decision",
    "action",
    "event",
    "perception",
    "lesson",
)

TYPE_DESCRIPTIONS = {
    "communication": "a message sent or received between members or people, e.g. 'Hello Z3'",
    "thought": "reasoning that commits to nothing: plans, predictions, questions, interpretations",
    "decision": "a commitment to act, or not to act, e.g. 'Slow to 0.1 m/s near Vector'",
    "action": "something the member did; set outcome when known, e.g. 'Drove forward 0.5 m'",
    "event": "something that happened to the member, not sensed or chosen, e.g. 'Docked'",
    "perception": "something sensed, outside or inside the member, e.g. 'Obstacle 12 cm ahead'",
    "lesson": "a conclusion drawn from experience, meant to change future behavior",
}

MAX_SUMMARY = 280
MAX_PAYLOAD_BYTES = 16 * 1024


class ToolError(Exception):
    """A tool-level error with a stable code and a human-safe message (no keys)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _payload_bytes(payload: dict | None) -> bytes:
    if payload is None:
        return b"{}"
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def remember(
    client: LTMSClient,
    type: str,
    summary: str,
    payload: dict | None = None,
    place: str | None = None,
    peers: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Sign one record and send it (append then flush); returns {"seq", "hash"}.

    The model never sees the member's key. If LTMS cannot be reached the signed
    record stays in the client's outbox and is retried on the next call.
    """
    if type not in ALLOWED_TYPES:
        raise ToolError("type_forbidden", f"type {type!r} is not allowed through the MCP server")
    if len(summary) > MAX_SUMMARY:
        raise ToolError("summary_too_long", f"summary exceeds {MAX_SUMMARY} characters")
    if payload is not None and len(_payload_bytes(payload)) > MAX_PAYLOAD_BYTES:
        raise ToolError("payload_too_large", f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")

    header: dict[str, Any] = {}
    if place is not None:
        header["place"] = place
    if peers is not None:
        header["peers"] = peers
    if tags is not None:
        header["tags"] = tags

    rec = client.append(type, payload=payload, summary=summary, **header)
    try:
        client.flush()
    except LTMSError as exc:
        # A rejection: turn it into a tool error with its code and message.
        raise ToolError(exc.code, exc.message) from None
    except httpx.HTTPError:
        # Transport failure: the record stays queued in the client's outbox and
        # is retried with the next call. Never expose the key.
        raise ToolError("queued", "record is queued and will be sent with the next call") from None
    return {"seq": rec["seq"], "hash": rec["hash"]}


def recall_context(
    client: LTMSClient, intent: str | None = None, target: str | None = None, place: str | None = None
) -> dict[str, Any]:
    """Return the context pack the member is allowed to see."""
    return client.context(intent=intent, target=target, place=place)


def search_records(
    client: LTMSClient,
    type: str | None = None,
    agent: str | None = None,
    place: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return records matching the filters, limit capped at 100."""
    limit = min(int(limit), 100)
    params: dict[str, Any] = {"limit": limit}
    if type is not None:
        params["type"] = type
    if agent is not None:
        params["agent"] = agent
    if place is not None:
        params["place"] = place
    return client.records(**params)
