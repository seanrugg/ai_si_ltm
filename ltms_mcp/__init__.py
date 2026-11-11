"""ltms_mcp: an MCP server so agent frameworks can join LTMS as members.

The tool logic lives here as plain functions that take an ``LTMSClient`` and
know nothing about MCP. ``ltms_mcp.server`` wraps them with the MCP SDK (v2) ``MCPServer``. Tests can
call these functions directly with no MCP transport installed.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from cli_agent.client import LTMSClient, LTMSError
from cli_agent.outbox import Outbox
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


def _prune_outbox_from(outbox: Outbox, agent: str, seq: int) -> None:
    """Drop queued records from ``seq`` onward. Plain SQL on the outbox DB."""
    outbox.conn.execute("DELETE FROM outbox WHERE agent = ? AND seq >= ?", (agent, seq))
    outbox.conn.commit()


def remember(
    client: LTMSClient,
    type: str,
    summary: str,
    payload: dict | None = None,
    place: str | None = None,
    peers: list[str] | None = None,
    tags: list[str] | None = None,
    outbox: Outbox | None = None,
) -> dict[str, Any]:
    """Sign one record and send it (append then flush); returns {"seq", "hash"}.

    The model never sees the member's key. If LTMS cannot be reached the signed
    record stays in the client's outbox and is retried on the next call. When an
    outbox is given, each appended record is also persisted there and pruned
    once the server acknowledges it.
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
    if outbox is not None:
        outbox.add(rec)
    try:
        client.flush()
    except LTMSError as exc:
        # A rejection: the server refused the record at the front of the queue
        # and nothing at or after it was accepted. Drop it and everything
        # queued after it, in memory and in the persistent outbox, then re-sync
        # so the next record re-signs from the server's head.
        rejected = min(r["seq"] for r in client.outbox)
        client.outbox = [r for r in client.outbox if r["seq"] < rejected]
        if outbox is not None:
            _prune_outbox_from(outbox, client.name, rejected)
        client.sync_head()
        raise ToolError(exc.code, f"record not stored: {exc.message}") from None
    except httpx.HTTPError:
        # Transport failure: the record stays queued in the client's outbox and
        # is retried with the next call. Never expose the key.
        raise ToolError("queued", "record is queued and will be sent with the next call") from None
    if outbox is not None:
        outbox.prune(client.name, client.seq)
    return {"seq": rec["seq"], "hash": rec["hash"]}


def restore(client: LTMSClient, outbox: Outbox) -> int:
    """Put the persistent outbox's pending records back into the client.

    Called once at startup after ``sync_head``; records the server has already
    acknowledged (``seq <= client.seq``) are skipped and pruned. Returns how
    many records were restored to the client's outbox.
    """
    client.sync_head()
    restored = 0
    for rec in outbox.pending(client.name, 1000):
        if rec["seq"] <= client.seq:
            continue
        client.outbox.append(rec)
        restored += 1
    outbox.conn.execute(
        "DELETE FROM outbox WHERE agent = ? AND seq <= ?", (client.name, client.seq)
    )
    outbox.conn.commit()
    return restored


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


# ---------------------------------------------------------------- datasets (#241)
DATASET_NOTE = "Reference material for the fleet. Cite what you use in a lesson's payload.sources."
MAX_ITEM_CHARS = 20_000


def _dataset_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LTMSError as exc:
        raise ToolError(exc.code, exc.message) from None


def resolve_dataset(client: LTMSClient, dataset: str) -> str:
    """A dataset id or name -> its id. Names match case-insensitively."""
    listed = _dataset_call(client.datasets)
    for ds in listed:
        if ds["id"] == dataset:
            return ds["id"]
    wanted = dataset.strip().casefold()
    for ds in listed:
        if ds["name"].casefold() == wanted:
            return ds["id"]
    raise ToolError("not_found", f"no approved dataset {dataset!r}; call list_datasets")


def list_datasets(client: LTMSClient) -> list[dict[str, Any]]:
    """The fleet's approved datasets: id, name, description, current version, licence."""
    keep = ("id", "name", "description", "kind", "licence", "current_version", "item_count")
    return [{k: ds.get(k) for k in keep if k in ds} for ds in _dataset_call(client.datasets)]


def search_datasets(client: LTMSClient, dataset: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Ranked hits (key, snippet) in one dataset's current version."""
    dataset_id = resolve_dataset(client, dataset)
    return _dataset_call(client.search_dataset, dataset_id, query, limit=max(1, min(int(limit), 50)))


def read_item(client: LTMSClient, dataset: str, key: str, version: int | None = None,
              cycle: str | None = None) -> dict[str, Any]:
    """One item's text (at most 20,000 characters) with its version and hash. Logged as a read."""
    dataset_id = resolve_dataset(client, dataset)
    item = _dataset_call(client.read_item, dataset_id, key, version=version, cycle=cycle)
    text = item.get("text") or ""
    out = {"dataset": dataset_id, "key": item["key"], "version": item["version"],
           "media_type": item.get("media_type"), "sha256": item.get("sha256"), "text": text[:MAX_ITEM_CHARS]}
    if len(text) > MAX_ITEM_CHARS:
        out["note"] = (f"text truncated to {MAX_ITEM_CHARS:,} of {len(text):,} characters; "
                       "search the dataset to find the part you need")
    if item.get("tier") == "external":
        # Not in LTMS: fetch it from the source, then call report_read (#248).
        out["locator"] = item.get("locator")
        out["integration"] = item.get("integration")
        out["note"] = "external item: fetch it from the integration, then call report_read"
    out["cite_as"] = {"dataset": dataset_id, "version": item["version"], "item": item["key"]}
    return out


def report_read(client: LTMSClient, dataset: str, version: int, item: str, cycle: str | None = None) -> dict:
    """Log a read of an item fetched from its source (an external item)."""
    dataset_id = resolve_dataset(client, dataset)
    return _dataset_call(client.report_read, dataset_id, version, item, cycle=cycle)
