# ltms_mcp — MCP server for LTMS

`ltms_mcp` lets agent frameworks (Claude Code, Hermes, OpenClaw and others)
join LTMS as members **without the model ever handling a private key or
building signed records**. The MCP server holds the member's key, signs and
sends records via `cli_agent.client.LTMSClient`, and exposes these tools:

* `remember(type, summary, payload, place, peers, tags)` — sign and send one
  record, returns `{"seq", "hash"}`.
* `recall_context(intent, target, place)` — the context pack the member may see.
* `search_records(type, agent, place, limit)` — records matching the filters.
* `list_datasets()` — the fleet's approved datasets (reference material).
* `search_datasets(dataset, query, limit)` — ranked hits with snippets; `dataset`
  is an id or a name.
* `read_item(dataset, key, version, cycle)` — one item's text, truncated to
  20,000 characters with a note when longer, plus `version`, `media_type`,
  `sha256` and `cite_as`. Every read is logged by LTMS (and sent to the LRS if
  one is configured), exactly like a robot's read. Cite what you use: put
  `cite_as` in a lesson's `payload.sources` (at most 16). See
  [docs/datasets.md](../docs/datasets.md).

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
      "cwd": "/opt/ltms",
      "env": {
        "LTMS_SERVER": "http://localhost:8080",
        "LTMS_KEY_PATH": "/opt/ltms/keys/agent-x.json"
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

## 6. Optional: look up standard terms in the I2IDL Glossary

The I2IDL Glossary has its own public, read-only MCP server at
`https://id.i2idl.org/mcp`. Its tools are `search_concepts`, `get_concept`,
`get_relationships` and `get_context`. Agents writing `lesson` and `policy`
records can use it to check the standard term for a concept. LTMS doesn't call
it and doesn't need it.

Add it beside the `ltms` entry:

```json
{
  "mcpServers": {
    "i2idl-glossary": { "type": "http", "url": "https://id.i2idl.org/mcp" }
  }
}
```

This needs outbound internet access. On a closed or restricted network, leave it out and use `docs/vocabulary.jsonld`, which lists the LTMS terms already matched to the glossary.

## Tests

```bash
pytest server/tests/test_mcp_tools.py
pytest
```

The plain functions in `ltms_mcp/__init__.py` need no MCP package; only the
optional server import is guarded by `pytest.importorskip("mcp")`.
