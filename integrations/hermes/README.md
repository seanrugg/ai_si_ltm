# Hermes Agent integration: `ltms_recorder`

A [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin that records what the agent **actually did** to LTMS: one signed record when a session starts, one for **every tool call** (with the tool's raw result), and one when the session ends. Once a day it also sends a [rollup](#daily-rollup) of usage counters.

The plugin runs from Hermes's own lifecycle hooks, so it records every call whether or not the model "decides" to. It records the tool's result as returned, not the model's summary of it. That makes it possible to tell real actions from text that only looks like them: a model that claims a result without calling the tool leaves no `action` record.

```
Hermes ──hooks──▶ ltms_recorder (holds the key, signs, queues) ──HTTPS──▶ LTMS
```

The model never sees the member's key.

## What is recorded

| When | Record | Tags | Payload |
|---|---|---|---|
| Session starts | `event` "Hermes session started" | `hermes,session` | `session_id`, `model`, `platform` |
| Each tool call | `action` "`<tool>` ok\|error (`<ms>` ms)" | `hermes,tool-call` | `tool_name`, `ok`, `duration_ms`, `session_id`, `task_id`, `model`, `record_mode`; in `full` mode also `args` (redacted) and `result_raw` |
| Session ends | `event` "Hermes session completed: N tool calls, M errors" | `hermes,session` | the counts, `completed`, `interrupted` |

- **`ok`** is a best guess. Hermes has no separate error flag, so a result counts as an error when it is a JSON object with a truthy `error`, `success: false`, `ok: false` or `isError: true`, or text that starts with `Error`.
- **`peers`.** When a tool name matches a `peer_map` prefix, the record's `peers` names the member the tool acted on, so the action also appears with that robot.
- **Limits.**
  - `result_raw` is cut to `max_result_bytes` (default 8192), with `result_truncated: true`.
  - `args` over 4 KB are kept as truncated text, with `args_truncated: true`.
  - A payload never exceeds 16 KB, and a summary never exceeds 280 characters.
- **Redaction.** Any argument whose name contains one of `redact_keys` (case-insensitive, at any depth) is stored as `[redacted]`.

### Modes (`record_mode`)

| Mode | Session records | Tool-call records | Tool arguments and results |
|---|---|---|---|
| `full` (default) | yes | yes | yes, redacted and truncated |
| `summary` | yes | yes | **no**: tool name, outcome and timing only |
| `off` | yes, with counts | no | no |

Use `summary` or `off` wherever tool content must not leave the device.

## Install

You need a running LTMS and Hermes Agent on the same machine as the plugin.

1. **Install the LTMS client into Hermes's Python.** The plugin imports `cli_agent`, which ships in the `ltms-harness` package (`harness/` in this repo). Install it into the Python environment Hermes runs in, not the system Python. For example, from a checkout of this repo:

   ```bash
   cd ~/.hermes/hermes-agent && uv pip install /path/to/ltms/harness
   ```

   Adjust the first path if your Hermes install lives elsewhere. `hermes plugins doctor` shows whether the plugin can load.

2. **Create a key and enroll the agent as a member** (with the client installed, from any shell):

   ```bash
   python -m cli_agent genkey --name <agent-name> --out ~/.hermes/ltms/<agent-name>.json
   python -m cli_agent enroll --key ~/.hermes/ltms/<agent-name>.json \
       --server https://ltms.example.org --kind agent --swarm <your-swarm> --role member
   python -m cli_agent pubkey --key ~/.hermes/ltms/<agent-name>.json   # fingerprint to check
   ```

   Then approve it in the dashboard (**Members**). Check that the public key matches the `pubkey` output before approving. `--swarm` and `--role` are a request shown to the admin, not a grant.

   Use a key **only for this plugin**. If another program (for example `ltms_mcp`) signs with the same key, the two will fork the member's chain.

3. **Link the plugin into Hermes:**

   ```bash
   ln -s /path/to/ltms/integrations/hermes/ltms_recorder ~/.hermes/plugins/ltms_recorder
   hermes plugins doctor
   ```

   Enable it if your Hermes version asks you to, for example in `hermes plugins` or `/plugins`.

4. **Set the environment** wherever Hermes starts, for example in Hermes's env file or the service that runs `hermes gateway`:

   ```bash
   LTMS_SERVER=https://ltms.example.org
   LTMS_KEY_PATH=/home/<user>/.hermes/ltms/<agent-name>.json
   ```

   If either is missing, the plugin logs a warning and stays off. Hermes still starts.

## Settings

Plugin settings live under `plugins.entries.ltms_recorder.settings` in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    ltms_recorder:
      settings:
        record_mode: full          # full | summary | off
        peer_map:                  # tool-name prefix -> the LTMS member it acts on
          "mcp__robot__": "<robot-member>"
        max_result_bytes: 8192     # at most 8192
        redact_keys: [key, token, secret, password, authorization, cookie]
        flush_interval_seconds: 5
        flush_batch: 20
        rollup: true               # daily usage rollup; false turns it off
```

Example: if a robot's MCP server is configured in Hermes as `vector`, its tools are named `mcp__vector__…`. Then `peer_map: {"mcp__vector__": "<that robot's member name>"}` shows every Hermes action on that robot in the robot's timeline as well.

## How it behaves

- **Hermes is never blocked or broken.** Hooks only add the event to an in-memory queue and return. A background thread signs and sends every `flush_interval_seconds`, or as soon as `flush_batch` events wait. Errors are logged without the key or payload, never raised.
- **Offline.**
  - Signed records are kept in `<HERMES_HOME>/plugin-data/ltms_recorder/outbox.db` and sent when LTMS is back.
  - The last acknowledged chain position is kept in `head.json` next to it. After a restart with no network, new records continue the chain from there.
  - On the very first start with no network, events wait in memory, up to 1,000, until LTMS can be reached.
- **Rejected records.** If LTMS rejects a record, that record and the ones signed after it are dropped (their chain depends on it), and the plugin re-syncs. The log names the rejection code.
- **Exit.** At shutdown the plugin makes one last attempt to send.
- **Sessions.** Tool calls carry the `session_id` of the most recently started session. Hermes runs one session at a time in the CLI. With several concurrent gateway sessions, treat `session_id` on tool calls as approximate.

## Daily rollup

Besides the detailed records, the plugin keeps **bounded usage counters** for each UTC day and sends them once, as one record. A rollup is built to be safe to share and easy to add up across members. It works in every `record_mode`, including `off`, so a deployment that must keep traces on the device can still report usage.

| Field | Counted from |
|---|---|
| `sessions.started / completed / interrupted` | session start and end |
| `tool_calls{name: {ok, error}}` | each tool call. Only MCP tool names (`mcp__…`) and Hermes built-in tool names are kept, up to 64; anything else counts under `other`. A model can invent a tool name, and an invented name is content |
| `tool_errors{reason: n}` | each failed tool call, classified into a **fixed list**: `timeout`, `not_found`, `invalid_args`, `refused`, `unavailable`, `upstream_error`, `other`. The result is read to classify it and never kept |
| `model_routes{"provider/model": {calls, input_tokens, output_tokens, api_seconds}}` | each model API call (`post_api_request`). A provider or model that looks like a URL or path becomes `custom` / `unknown`; at most 16 routes |
| `api_errors{4xx, 5xx, network, other}` | failed model API calls (`api_request_error`) |
| `compactions` | context compactions (`post_compaction`) |
| `machine` | `arch`, `os`, RAM in whole GB, `hermes_version`, `plugin_version` |

**Never in a rollup:** prompts, messages, tool arguments or results, file paths, setting values, error text, URLs.

**The record:** a `perception` tagged `telemetry,internal,agent-rollup`, summary `daily rollup <date>: <sessions> sessions, <calls> tool calls, <errors> errors`, payload `{"rollup_schema": 1, "date": "<date>", …counters}`. It shows in the Timeline with the telemetry filter on.

**How it is kept and sent:**
- The day's counters are saved to `<HERMES_HOME>/plugin-data/ltms_recorder/rollup-<date>.json` after every update, so a restart keeps the day.
- At the first hook after midnight UTC, the finished day is signed into the chain (local work only; hooks never use the network) and the recorder's thread sends it. The day file is deleted once LTMS has accepted it. If the chain isn't ready yet, for example while offline, the file waits for a later hook or for exit.
- If Hermes stops between signing the rollup and saving that it was signed, that day can be sent twice. The record's date says which day it covers.

## Not included

- Recording model replies or prompts.
- OpenClaw. A sibling plugin would use the same pattern.
