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
- **The `tool_call` bridge.** With tool search on, Hermes runs MCP tools through its `tool_call` bridge, and the hooks see the bridge. A bridge call that runs one tool is recorded as **that tool**, with its own arguments and `"via": "tool_call"` in the payload. A bridge call that runs several keeps the name `tool_call` and lists them in `calls`. `peer_map` and the rollup use the real tool names.
- **`peers`.** When a tool name matches a `peer_map` prefix, the record's `peers` names the member the tool acted on. The action then also counts for that robot: on its profile (Top peers), in Conversation, in the xAPI export, and on its Timeline with **Include records naming this member** checked.
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

You need a running LTMS and Hermes Agent (tested with v0.23.3) on the same machine as the plugin, and a checkout of this repository. The checkout can be read-only to the user running Hermes, for example one that a hub's service user owns.

1. **Create a key and enroll the agent as a member.** Run this from the checkout with any Python 3.11+, using `uv` to supply the two libraries the client needs:

   ```bash
   cd /path/to/ltms
   uv run --no-project --with pynacl --with httpx python -m cli_agent genkey --name <agent-name> --out ~/.hermes/ltms/<agent-name>.json
   uv run --no-project --with pynacl --with httpx python -m cli_agent enroll --key ~/.hermes/ltms/<agent-name>.json \
       --server https://ltms.example.org --kind agent --swarm <your-swarm> --role member
   uv run --no-project --with pynacl --with httpx python -m cli_agent pubkey --key ~/.hermes/ltms/<agent-name>.json
   ```

   Then approve the member in the dashboard (**Members**), after checking that its public key matches the `pubkey` output. `--swarm` and `--role` are a request shown to the admin, not a grant.

   Use the key **only for this plugin**. If another program (for example `ltms_mcp`) signs with the same key, the two will fork the member's chain. Keep the key file private (`chmod 700 ~/.hermes/ltms`).

2. **Link the plugin and enable it.** Hermes plugins are opt-in: a linked plugin does nothing until it is enabled.

   ```bash
   ln -s /path/to/ltms/integrations/hermes/ltms_recorder ~/.hermes/plugins/ltms_recorder
   hermes plugins enable ltms_recorder
   ```

   Enabling installs the plugin's dependencies (`pynacl`, `httpx`, from `pyproject.toml` beside the plugin) into Hermes's own environment. The LTMS client code (`cli_agent`, `ltms_contract`) is not installed: the plugin loads it from the checkout it is linked from, so `git pull` updates the plugin and the client together.

3. **Set the two variables in `~/.hermes/.env`.** Hermes and its gateway read this file when they start. An `export` in your shell does not reach a running gateway.

   ```bash
   LTMS_SERVER=https://ltms.example.org
   LTMS_KEY_PATH=/home/<user>/.hermes/ltms/<agent-name>.json
   ```

4. **Restart the gateway** (`hermes gateway restart`), start a new chat, and check the log:

   ```bash
   grep ltms_recorder ~/.hermes/logs/agent.log | tail -3
   ```

   Look for `recording as <agent-name> (mode full, rollup on)` and then `chain ready (online, seq …)`. Each session then shows on the member's Timeline: a session start, one `action` per tool call, a session end.

**Copying the plugin instead of linking it?** Then it can't find the client in a checkout. Install `ltms-harness` (`harness/` in this repo) into **Hermes's environment** instead: the `site-packages` folder that `hermes --run-module site` lists under `environments/`, as `uv pip install --python <that environment>/bin/python …`. Hermes may rebuild that environment when plugins change, which drops a manual install, so linking is the better choice. If the checkout is read-only, build from a scratch copy of `harness/`, `cli_agent/` and `ltms_contract/`: the build writes an `egg-info` folder beside `harness/`.

### Troubleshooting

| What you see | Why | Fix |
|---|---|---|
| `hermes plugins doctor` passes but nothing is recorded | The plugin isn't enabled. Doctor loads a plugin to test it even when it's disabled | `hermes plugins enable ltms_recorder`; `hermes plugins list` shows the status |
| Doctor reports `0 hook(s)` | Doctor runs without your environment, so the plugin finds no `LTMS_SERVER` and registers nothing | Expected; check the log after a real session instead |
| `not started, LTMS_SERVER is not set` | The variables aren't in `~/.hermes/.env`, or the gateway started before they were added | Add them to `~/.hermes/.env` and restart the gateway |
| `not started, cannot import nacl` (or `httpx`) | Hermes's environment lacks a dependency, for example after it was rebuilt | `hermes plugins enable ltms_recorder` again, then restart the gateway |
| `not started, cannot import cli_agent` | The plugin isn't linked from a checkout and `ltms-harness` isn't installed in Hermes's environment | Link it from a checkout (step 2), or see "Copying the plugin" above |
| `Provider temporarily unavailable` in chat | The model server is slow or out of memory; not the plugin | Check the model server (for Ollama: `ollama ps`, `free -h`) |

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

## `tool_guard`: limits on tool arguments

A second, separate plugin in this folder. A model can send any number to a tool, and some tools pass it straight to hardware. VectorClaw's `vector_drive` drives `distance_mm` as given, so a made-up `2000` drives two metres. `tool_guard` checks each call in Hermes's `pre_tool_call` hook, **before the tool runs**, and blocks a call whose argument is out of range. Hermes returns the block message to the model as the tool's result, for example:

> Error: refused by tool_guard before mcp__vector__vector_drive ran: distance_mm=2000 is outside the limit of ±100; use a value between -100 and 100. The robot did not move.

Install it like the recorder: link `integrations/hermes/tool_guard` into `~/.hermes/plugins/tool_guard`, run `hermes plugins enable tool_guard`, and restart the gateway. It needs no variables and no dependencies. The log shows `tool_guard: guarding vector_drive (distance_mm ±100, angle_deg ±90, speed ±100)`.

**Limits** (`plugins.entries.tool_guard.settings.limits` in `~/.hermes/config.yaml`): tool name → `{argument: largest allowed absolute value}`. A bare name such as `vector_drive` also matches `mcp__<server>__vector_drive` for every server. Setting `limits` replaces the defaults; `{}` turns the guard off.

```yaml
plugins:
  entries:
    tool_guard:
      settings:
        limits:
          vector_drive: {distance_mm: 100, angle_deg: 90, speed: 100}   # the default
```

- **Calls through the `tool_call` bridge.** With tool search on (for example `hermes chat -t mcp-<server>`), Hermes rewrites a call to an MCP tool into its `tool_call` bridge *before* the hooks run: the hook sees `tool_call` with `{"calls": [{"name": "mcp__vector__vector_drive", "arguments": {…}}]}`. The guard unwraps it and checks every inner call. One bad call blocks the whole bridge call, and so does a bridge call whose arguments it can't read.
- **Rejected values:** a value that isn't a finite number (text, `true`, infinity) is refused too.
- **Missing arguments** aren't checked: the tool's own defaults apply.
- **Fail-closed:** if the guard itself fails while checking a guarded tool, it blocks the call. Other tools are never touched.
- **With the recorder,** a blocked call is recorded as a failed tool call, since its result starts with `Error:`, if Hermes runs `post_tool_call` for blocked calls. Check this on your Hermes version with one deliberate out-of-range call.

The guard is a backstop, not the whole safety story: keep a narrow tool list for routines, and rely on the robot's own protections (Vector's cliff sensors, stopping when lifted).

## Not included

- Recording model replies or prompts.
- OpenClaw. A sibling plugin would use the same pattern.
