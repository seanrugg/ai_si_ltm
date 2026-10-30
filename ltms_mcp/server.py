"""MCP server for LTMS: agent frameworks join as members without touching the key.

Run with stdio transport::

    LTMS_SERVER=http://localhost:8080 LTMS_KEY_PATH=keys/agent.json \
        python -m ltms_mcp.server

Configuration comes from the environment only:

* LTMS_SERVER   LTMS base URL, e.g. http://localhost:8080
* LTMS_KEY_PATH path to a key file from ``python -m cli_agent agent genkey``

The server holds the member's key and uses ``cli_agent.client.LTMSClient`` to
sign and send. The connected model never sees the key.
"""
from __future__ import annotations

import logging
import os

import anyio

from cli_agent.client import LTMSClient, load_key
from cli_agent.outbox import Outbox
from mcp.server.mcpserver.server import MCPServer

from ltms_mcp import remember as _remember
from ltms_mcp import recall_context as _recall_context
from ltms_mcp import restore as _restore
from ltms_mcp import search_records as _search_records

mcp = MCPServer(
    name="ltms-mcp",
    instructions="LTMS memory tools for agent frameworks. The server holds the member's key; models never see it.",
)

_CLIENT: LTMSClient | None = None
_OUTBOX: Outbox | None = None


def _client() -> LTMSClient:
    """Build the client once, sync the head, and restore pending records.

    When ``LTMS_OUTBOX_PATH`` is set, records persisted to the outbox are
    reloaded into the client at startup so nothing is lost on restart.
    """
    global _CLIENT, _OUTBOX
    if _CLIENT is None:
        name, key = load_key(os.environ["LTMS_KEY_PATH"])
        client = LTMSClient(os.environ["LTMS_SERVER"], signing_key=key, name=name)
        outbox_path = os.environ.get("LTMS_OUTBOX_PATH")
        _OUTBOX = Outbox(outbox_path) if outbox_path else None
        if _OUTBOX is not None:
            restored = _restore(client, _OUTBOX)  # restore() syncs the head
            logging.info("MCP: restored %d pending records from %s", restored, outbox_path)
        else:
            client.sync_head()
        _CLIENT = client
    return _CLIENT


@mcp.tool()
def remember(
    type: str,
    summary: str,
    payload: dict | None = None,
    place: str | None = None,
    peers: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Remember something as a signed LTMS record.

    Allowed types: communication, thought, decision, action, event, perception,
    lesson (policy and system are refused). summary max 280 chars; payload max
    16 KB serialized. Returns {"seq", "hash"}.
    """
    return _remember(_client(), type, summary=summary, payload=payload, place=place,
                     peers=peers, tags=tags, outbox=_OUTBOX)


@mcp.tool()
def recall_context(
    intent: str | None = None,
    target: str | None = None,
    place: str | None = None,
) -> dict:
    """Return the context pack (outcomes, comms, lessons, policies) the member may see."""
    return _recall_context(_client(), intent=intent, target=target, place=place)


@mcp.tool()
def search_records(
    type: str | None = None,
    agent: str | None = None,
    place: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return records matching the filters; limit is capped at 100."""
    return _search_records(_client(), type=type, agent=agent, place=place, limit=limit)


def main() -> None:
    anyio.run(mcp.run_stdio_async)


if __name__ == "__main__":
    main()
