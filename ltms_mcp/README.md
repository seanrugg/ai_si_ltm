# ltms-mcp

An [MCP](https://modelcontextprotocol.io) server so agent frameworks (Claude
Code, Hermes, OpenClaw and others) can join [LTMS](../README.md) as members
without the model ever handling a private key or building signed records. The
server holds the member's key, signs and sends records through
``cli_agent/client.py`` (``LTMSClient``), and exposes a few simple tools.

```
LLM ──tools──▶ ltms-mcp (holds the key, uses LTMSClient) ──HTTPS──▶ LTMS
```

## 1. Generate a member key

```bash
python -m cli_agent.agent genkey --name agent-x --out keys/agent-x.json
```

This writes an Ed25519 key file (mode 0600). Keep it off shared storage and out
of git.

## 2. Enroll as an agent

```bash
python -m cli_agent.agent enroll \
    --key keys/agent-x.json \
    --server http://localhost:8080 \
    --kind agent \
    --permissions contribute,recall
```

Enrollment is pending until an admin approves it.

## 3. Get approved in the dashboard

Open the dashboard (Members → Pending), find ``agent-x``, and approve it. Check
the key fingerprint against the public key in ``keys/agent-x.json`` so you
approve the right key. Grant ``contribute`` and ``recall`` (never ``policy`` or
``system`` — those are operator-only).

## 4. Run the MCP server

```bash
export LTMS_SERVER=http://localhost:8080
export LTMS_KEY_PATH=keys/agent-x.json
python -m ltms_mcp.server
```

It runs on stdio, so it plugs straight into an MCP client such as Claude Code.

## 5. Add it to Claude Code's MCP config

In Claude Code's MCP settings (``~/.claude.json`` or the project MCP config), add:

```json
{
  "mcpServers": {
    "ltms": {
      "command": "python",
      "args": ["-m", "ltms_mcp.server"],
      "cwd": "/path/to/ai_si_ltm",
      "env": {
        "LTMS_SERVER": "http://localhost:8080",
        "LTMS_KEY_PATH": "/path/to/ai_si_ltm/keys/agent-x.json"
      }
    }
  }
}
```

## Tools

- ``remember(type, summary, payload?, place?, peers?, tags?)`` — sign one
  record and send it (append then flush), returns ``{"seq", "hash"}``. Allowed
  types: communication, thought, decision, action, event, perception, lesson.
  ``policy`` and ``system`` are refused (operator-only). A summary over 280
  characters or a payload over 16 KB is refused.
- ``recall_context(intent?, target?, place?)`` — returns a context pack
  (outcomes, comms, lessons, policies).
- ``search_records(type?, agent?, place?, limit=20)`` — returns the member's
  records; limit is capped at 100.

The model never sees the private key: it is not returned, logged, or placed in
an error message.

## Install

The MCP server needs the optional ``mcp`` dependency:

```bash
pip install 'ltms[mcp]'
```
