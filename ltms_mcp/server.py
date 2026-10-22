"""MCP server that lets agent frameworks join LTMS as members.

Configuration comes from the environment only:

  LTMS_SERVER   URL of the LTMS server
  LTMS_KEY_PATH path to a key file from ``python -m cli_agent.agent genkey``

Run with stdio transport:

  python -m ltms_mcp.server

The tool logic is in :mod:`ltms_mcp` (plain functions taking an LTMSClient);
this module only wraps them with FastMCP and holds the member's key.
"""

from __future__ import annotations

import os
import sys

from cli_agent.client import LTMSClient, LTMSError

from ltms_mcp import (
    ALLOWED_TYPES,
    MCPError,
    MAX_PAYLOAD_BYTES,
    MAX_SUMMARY,
    remember as _remember,
    recall_context as _recall_context,
    search_records as _search_records,
    TYPE_DESCRIPTIONS,
)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - mcp is an optional dependency
    FastMCP = None

mcp = FastMCP("ltms-mcp") if FastMCP is not None else None

_client: LTMSClient | None = None


def _get_client() -> LTMSClient:
    """Return the member's client, logging in and syncing the chain head once."""
    global _client
    if _client is None:
        server = os.environ.get("LTMS_SERVER")
        key_path = os.environ.get("LTMS_KEY_PATH")
        if not server or not key_path:
            raise MCPError("config_error", "LTMS_SERVER and LTMS_KEY_PATH must be set")
        _client = LTMSClient(server, key_path=key_path)
        _client.login()
        _client.sync_head()  # continue the member's existing chain
    return _client


def _as_tool_error(exc: Exception) -> MCPError:
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, LTMSError):
        return MCPError(exc.code, exc.message)
    return MCPError("tool_error", str(exc))


if mcp is not None:

    @mcp.tool()
    def remember(
        type: str,
        summary: str,
        payload: dict | None = None,
        place: str | None = None,
        peers: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Sign one record and send it (append then flush), returning {"seq", "hash"}."""
        client = _get_client()
        try:
            return _remember(
                client,
                type=type,
                summary=summary,
                payload=payload,
                place=place,
                peers=peers,
                tags=tags,
            )
        except LTMSError as exc:
            raise MCPError(exc.code, exc.message) from None

    @mcp.tool()
    def recall_context(
        intent: str | None = None,
        target: str | None = None,
        place: str | None = None,
    ) -> dict:
        """Return a context pack for the member."""
        client = _get_client()
        try:
            return _recall_context(client, intent=intent, target=target, place=place)
        except LTMSError as exc:
            raise MCPError(exc.code, exc.message) from None

    @mcp.tool()
    def search_records(
        type: str | None = None,
        agent: str | None = None,
        place: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Return the member's records, limit capped at 100."""
        client = _get_client()
        try:
            return _search_records(client, type=type, agent=agent, place=place, limit=limit)
        except LTMSError as exc:
            raise MCPError(exc.code, exc.message) from None


def main() -> int:
    if FastMCP is None:
        print("the mcp package is not installed: pip install 'ltms[mcp]'", file=sys.stderr)
        return 2
    if not os.environ.get("LTMS_SERVER") or not os.environ.get("LTMS_KEY_PATH"):
        print("set LTMS_SERVER and LTMS_KEY_PATH", file=sys.stderr)
        return 2
    # Log in and sync head once at startup so seq and prev_hash continue the
    # member's existing chain.
    _get_client()
    mcp.run()  # stdio transport
    return 0


if __name__ == "__main__":
    sys.exit(main())
