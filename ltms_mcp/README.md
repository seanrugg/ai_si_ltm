# ltms_mcp — MCP server for LTMS

`ltms_mcp` lets agent frameworks (Claude Code, Hermes, OpenClaw and others)
join LTMS as members **without the model ever handling a private key or
building signed records**. The MCP server holds the member's key, signs and
sends records via `cli_agent.client.LTMSClient`, and exposes three tools:

* `remember(type, summary, payload, place, peers, tags)` — sign and send one
  record, returns `{"seq", "hash"}`.
* `recall_context(intent, target, place)` — the context pack the member may see.
* `search_records(type, agent, place, limit)` — records matching the filters.

## 1. Generate a member key

```bash
python -m cli_agent agent genkey --name agent-x --out keys/agent-x.json
```

This prints the key's public fingerprint. Keep `keys/agent-x.json` private — it
is the member's identity.

## 2. Enroll and get approved

```bash
python -m cli_agent agent enroll --key keys/agent-x.json --server http://localhost:8080 \
                                 --kind agent --permissions contribute,recall
```

Then approve the member in the dashboard (Members → Pending), checking that the
fingerprint matches the key file you generated. An approved `agent` member with
`contribute` and `recall` can write and read through the MCP server.

## 3. Run the MCP server

```bash
LTMS_SERVER=http://localhost:8080 LTMS_KEY_PATH=keys/agent-x.json \
  python -m ltms_mcp.server
```

Configuration comes from the environment only (`LTMS_SERVER`, `LTMS_KEY_PATH`).
The server calls `client.sync_head()` at startup so the member's chain continues
from its existing head. It runs stdio transport, ready for an MCP client.

## 4. Add to Claude Code's MCP config

Add to your Claude Code MCP settings (e.g. `~/.claude/settings.json` or the
project's MCP config):

```json
{
  "mcpServers": {
    "ltms": {
      "command": "python",
      "args": ["-m", "ltms_mcp.server"],
      "cwd": "/Users/aiadmin/qwen/ai_si_ltm",
      "env": {
        "LTMS_SERVER": "http://localhost:8080",
        "LTMS_KEY_PATH": "/Users/aiadmin/qwen/ai_si_ltm/keys/agent-x.json"
      }
    }
  }
}
```

## 5. Allowed record types

`remember` accepts: `communication`, `thought`, `decision`, `action`, `event`,
`perception`, `lesson`. `policy` and `system` are refused — policy comes from an
operator, and `system` is reserved for the server. Summaries are capped at 280
characters and payloads at 16 KB serialized.

If LTMS is unreachable, the signed record stays in the client's outbox and is
retried on the next call; the model is told the record is queued.

## Tests

```bash
pytest server/tests/test_mcp_tools.py
pytest
```

The plain functions in `ltms_mcp/__init__.py` need no MCP package; only the
optional server import is guarded by `pytest.importorskip("mcp")`.
