"""ltms_mcp: an MCP server so agent frameworks can join LTMS as members.

The tool logic lives in plain functions (``remember``, ``recall_context``,
``search_records``) that take an :class:`cli_agent.client.LTMSClient`, so they
can be tested with no MCP transport. :mod:`ltms_mcp.server` wraps them with
FastMCP and runs them on stdio.
"""

from __future__ import annotations

import json

import httpx

from cli_agent.client import LTMSClient

__version__ = "0.1.0"

# --- configuration -------------------------------------------------------

MAX_SUMMARY = 280
MAX_PAYLOAD_BYTES = 16 * 1024

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
    "communication": "a message sent or received between members or people",
    "thought": "reasoning that commits to nothing: plans, predictions, questions, interpretations",
    "decision": "a commitment to act, or not to act",
    "action": "something the member did; set outcome when known",
    "event": "something that happened to the member, not sensed or chosen",
    "perception": "something sensed, outside or inside the member",
    "lesson": "a conclusion drawn from experience, meant to change future behavior",
}


class MCPError(Exception):
    """A tool error returned to the model: an MCP-style code and a human message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- tool logic (plain functions, take an LTMSClient) --------------------


def remember(
    client: LTMSClient,
    type: str,
    summary: str,
    payload: dict | None = None,
    place: str | None = None,
    peers: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Sign one record and send it (append then flush), returning {"seq", "hash"}.

    Allowed types (one-line help for the model):
      communication — a message sent or received between members or people
      thought — reasoning that commits to nothing: plans, predictions, questions, interpretations
      decision — a commitment to act, or not to act
      action — something the member did; set outcome when known
      event — something that happened to the member, not sensed or chosen
      perception — something sensed, outside or inside the member
      lesson — a conclusion drawn from experience, meant to change future behavior

    ``policy`` and ``system`` are refused: they come from an operator, not a model.
    A summary over 280 characters or a payload over 16 KB (once serialized) is refused.
    """
    if type not in ALLOWED_TYPES:
        raise MCPError(
            "refused",
            f"type {type!r} is not allowed for remember. "
            f"Allowed: {', '.join(ALLOWED_TYPES)}. policy and system are operator-only.",
        )
    if summary is not None and len(summary) > MAX_SUMMARY:
        raise MCPError(
            "refused",
            f"summary is {len(summary)} characters; limit is {MAX_SUMMARY}",
        )
    if payload is not None:
        serialized = json.dumps(payload, separators=(",", ":"))
        size = len(serialized.encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise MCPError(
                "refused",
                f"payload is {size} bytes; limit is {MAX_PAYLOAD_BYTES} bytes (16 KB)",
            )
    rec = client.append(
        type,
        summary=summary,
        payload=payload,
        place=place,
        peers=peers,
        tags=tags,
    )
    try:
        client.flush()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # The server is unreachable: the record stays in the client's outbox
        # and will be sent with the next call. Nothing is lost.
        return {
            "queued": True,
            "seq": rec["seq"],
            "message": "server unreachable; record is queued and will be sent with the next call",
        }
    return {"seq": rec["seq"], "hash": rec["hash"]}


def recall_context(
    client: LTMSClient,
    intent: str | None = None,
    target: str | None = None,
    place: str | None = None,
) -> dict:
    """Return a context pack for the member: ``client.context(...)``."""
    return client.context(intent=intent, target=target, place=place)


def search_records(
    client: LTMSClient,
    type: str | None = None,
    agent: str | None = None,
    place: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return the member's records (``client.records(...)``), limit capped at 100."""
    return client.records(type=type, agent=agent, place=place, limit=min(int(limit), 100))


__all__ = [
    "ALLOWED_TYPES",
    "MAX_PAYLOAD_BYTES",
    "MAX_SUMMARY",
    "MCPError",
    "TYPE_DESCRIPTIONS",
    "remember",
    "recall_context",
    "search_records",
]
