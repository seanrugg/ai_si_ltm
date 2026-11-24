# App Lab member: an Arduino App Lab board as an LTMS swarm member

An [Arduino App Lab](https://docs.arduino.cc/software/app-lab/) app (#299, part of #289)
that makes a board with Linux and an MCU (UNO Q, VENTUNO Q and the like) a member of an
LTMS swarm:

- **Python side (Linux):** runs `ltms-harness`'s embeddable member
  (`cli_agent.member_main.MemberNode`, #491) from App Lab's loop. That means signed,
  hash-chained records; MQTT to the swarm's hub with an outbox, acks and resend; direct
  HTTPS to LTMS when the hub is down (`TRANSPORT=auto`, #297); heartbeats; answers to admin
  pings; introductions; and signed directives (#306, #490).
- **Sketch (MCU):** owns safety and the status lights. Every second it sends Python a
  status line. The Python side turns those into **one** `perception` record a minute, tagged
  `internal,telemetry`, with min/max/last per value. If Python stops answering for 5 seconds,
  the sketch shows a fixed "disconnected" state by itself.

Nothing in the defaults is specific to a site or a board. Board-specific lights are one
`#if` block per board in the sketch; the VENTUNO Q has the first.

## What you need

- An App Lab board, updated (`arduino-app-cli system update`).
- An LTMS server, and a swarm with a hub (Mosquitto plus `ltms-hub`). See `docs/swarms.md`.
- On the hub: an MQTT login for this board. Its ACL must allow publishing to
  `<topic id>/<name>/{records,say,hello}`, and subscribing to `<topic id>/<name>/{acks,ping}`,
  `<topic id>/+/say` and `<topic id>/fleet/#`.
- On your computer: Python 3.11+ with pip, to build the app.

## Install

1. **Build the app** from the repository root:
   ```bash
   examples/app-lab-member/make-app.sh
   ```
   This builds `ltms-harness` from `harness/` and unpacks it into `python/vendor`, so the
   board doesn't download the harness. App Lab installs the harness's three dependencies
   (`pynacl`, `httpx`, `paho-mqtt`) from `python/requirements.txt`. Output:
   `examples/app-lab-member/dist/ltms-member.zip` (and the folder next to it).
2. **Import it:** App Lab → My Apps → Import → `ltms-member.zip`. Or copy the
   `ltms-member` folder to `~/ArduinoApps/` on the board.
3. **First run:** press Run. The app writes the settings file `data/member.env` in the app's
   folder, logs "Not configured yet", and the status light shows *waiting*.
4. **Settings:** edit `data/member.env` on the board (for example over SSH:
   `nano ~/ArduinoApps/ltms-member/data/member.env`).
   - **Who it is:** `MEMBER_NAME` (permanent, lowercase), `OPERATOR`, `ROLE` (`member` or
     `observer`).
   - **Where it connects:** `LTMS_SERVER`, `MQTT_HOST`, `MQTT_PASSWORD`, and `SWARM_TOPIC_ID`
     (leave it empty to look it up from LTMS after approval).
   - **Enrollment:** `ENROLL_SWARM` (and `INVITE` if you have a code). Then set
     `ENROLL=request`.

   Restart the app.
5. **Approve:** the app makes its key, logs the **public key**, and sends one enrollment:
   `kind`, `permissions`, operator, intended swarm and role (#295), and a note with the
   hardware summary (#296). In the dashboard, check that the first 16 characters of the key
   match the app's log, approve it, and add it to the swarm. The app signs in once a minute
   and starts the member by itself once approved.
6. **Check:** the status light turns *online*. In LTMS, the member's timeline shows a
   `perception` "MCU status (1 min)" every minute and a heartbeat every
   `HEARTBEAT_SECONDS`. Pull the hub's network cable: the light goes to *no hub*, and the
   records reach LTMS directly (or wait in the outbox) and catch up when the hub is back.

## Settings (`data/member.env`)

| Setting | Default | Meaning |
|---|---|---|
| `MEMBER_NAME` | (required) | the member's permanent name |
| `MEMBER_KIND` | `robot` | `robot`, `agent`, `assistant` |
| `OPERATOR` | | who is accountable for this board |
| `ROLE` | `member` | `member` or `observer`: what you ask for. The admin decides |
| `PERMISSIONS` | `contribute` | what you ask for, comma-separated |
| `LTMS_SERVER` | placeholder | e.g. `https://ltms.example.org` |
| `ENROLL` | `hold` | `request` sends the enrollment (once) |
| `ENROLL_SWARM`, `INVITE` | | the intended swarm's name, and an invite code |
| `TRANSPORT` | `auto` | `mqtt`, `direct` or `auto` (direct when the hub is down) |
| `MQTT_HOST`, `MQTT_PORT` | placeholder, `1883` | the swarm's hub |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | `MEMBER_NAME`, (none) | the hub login |
| `SWARM_TOPIC_ID` | (looked up) | `sw-xxxxxxxx` |
| `HEARTBEAT_SECONDS` | `600` | |

Every other `ltms-member` setting works too: see `cli_agent/member_main.py`. Environment
variables override the file.

## What's in `data/`, and security

`data/` is the app's private data folder (mode 700):

| File | What it is |
|---|---|
| `member.env` | the settings, including `MQTT_PASSWORD` (mode 600) |
| `keys/<name>.key.json` | the member's private key (mode 600). It is made on the board and never leaves it |
| `member-outbox.db` | signed records not yet acknowledged (SQLite) |
| `member-head.json` | the chain head |
| `enrolled.json`, `approved.json` | the enrollment and approval steps (delete both to enroll again) |
| `fleet-key.pub`, `directives.json`, `introduced.json` | the fleet key cache, acknowledged directives, last introduction |

**Empty `data/` before you export or share the app.** It holds the key and the password.
Settings are kept here, not in App Lab brick variables, because those go out with an export.

## Status lights

| Link state | Any board (`LED_BUILTIN`) | VENTUNO Q (RGB LED 2) |
|---|---|---|
| starting | slow blink | cyan |
| waiting (settings, enrollment, approval) | slow blink | yellow |
| online | on | green |
| no hub (records go direct or wait in the outbox) | off | red |
| **disconnected**: Python silent for 5 s (the sketch decides this alone) | fast blink | red fast blink, and an X on the LED matrix |

On the VENTUNO Q, RGB LED 1 (its red is Zephyr's error LED) and LED 4 (its red is the CAN
state LED) are left alone. **Adding a board:** add an `#elif defined(ARDUINO_<BOARD>)` block
in `sketch.ino`, using that board's LED names from its Zephyr variant. Anything else on the
Python side works on any App Lab board.

## On-board agent (optional, #492)

Off by default. With `AGENT=on`, the board takes **tasks** and works on each one with a
model, a tool at a time. It's an option of this app, not a separate app or a role: a
member or an observer can have it.

**Where tasks come from** (`AGENT_TASKS_FROM`, default `directives,swarm`):

| Source | What counts as a task | Where the answer goes |
|---|---|---|
| `directives` | a verified directive's text. The directive is accepted and queued; if the queue is full or the issuer has sent too many, it's **declined** with that reason | a `communication` tagged `directive-report`, refs the member's "received" ack |
| `swarm` | a swarm message **addressed to this member**: `peers` names it, or `@<name>` is in the text | a swarm reply (`reply_to` the message, `peers` the sender), tagged `agent-reply` |
| `console` | a line typed into the local console (`CONSOLE=on`), signed first as an operator `communication` tagged `console` | a `communication` with `reply_to` that line, and on the page |

**Loop protection:** introductions and directive acks are never tasks; a reply to one of
this member's own messages is only a task if its text says `@<name>` again; each sender
gets `AGENT_PER_SENDER_HOUR` tasks an hour; the queue holds `AGENT_QUEUE`.

**Every step is signed.** A tool call is a `decision` (refs `because` the task input), then
an `action` (refs the `decision`, `outcome` success or failed). A tool that doesn't exist,
bad arguments, or the MCU refusing are failed actions, and the error goes back to the model.
After `AGENT_MAX_STEPS` tool calls without an answer, the task ends as failed.

**Tools:** `board_status`; `set_light` (off, busy, attention, done) and `show_icon` (blank,
check, question, heart, dot), both **through the MCU, which can refuse** (no spare LED or
display, a bad value, or "disconnected" showing); `recall` (the fleet context pack; the
member needs the `recall` permission); `say` (at most `AGENT_SAY_PER_TASK` per task);
`note` (a private `thought`). **Nothing moves:** there are no actuation tools.

**The model** returns one JSON object: `{"tool": ..., "args": {...}}` or `{"answer": ...}`.
Code fences, `<think>` blocks and text around it are tolerated; anything else gets one
"that wasn't valid" retry, then the task ends as failed. Text from tasks, other members,
records and recall is data, never instructions: the system prompt says so, and the tools
can't do anything the agent isn't allowed to do anyway.

| `AGENT_BACKEND` | Model |
|---|---|
| `brick` (default) | App Lab's **Large Language Model** Brick. Add it to the app first (App Lab: *Add Brick*); `AGENT_MODEL` empty uses the Brick's configured model. Checked against `arduino/app-bricks-py` (`LargeLanguageModel(...).chat(text)`), not yet run on a board |
| `openai` | any OpenAI-compatible server at `AGENT_URL` (ending in `/v1`): llama.cpp, Ollama, vLLM. `AGENT_MODEL` and `AGENT_API_KEY` if it needs them |
| `off` | no agent |

The model runs on a worker thread; every record write and MCU call is handed to the App
Lab loop (`MemberNode.submit`), so the member's outbox and the Bridge stay single-threaded.

**The console has no login.** With `CONSOLE=on`, anyone who can reach
`CONSOLE_BIND:CONSOLE_PORT` can give this board tasks, and each one is signed into its
memory as an operator message. `CONSOLE_BIND` defaults to `127.0.0.1`, which in App Lab's
container is the container itself; reaching it from the LAN (`0.0.0.0`) depends on how App
Lab maps the app's ports, which isn't verified yet. Leave it off unless you need it.

| Setting | Default | |
|---|---|---|
| `AGENT` | `off` | `on` to take tasks |
| `AGENT_BACKEND`, `AGENT_URL`, `AGENT_MODEL`, `AGENT_API_KEY` | `brick`, empty | see above |
| `AGENT_TASKS_FROM` | `directives,swarm` | any of `directives`, `swarm`, `console` (`CONSOLE=on` adds `console`) |
| `AGENT_MAX_STEPS` | `6` | tool calls per task |
| `AGENT_QUEUE` | `5` | tasks waiting |
| `AGENT_PER_SENDER_HOUR` | `6` | tasks per sender per hour |
| `AGENT_SAY_PER_TASK` | `2` | `say` calls per task |
| `AGENT_MODEL_SECONDS` | `120` | time limit for one model call |
| `CONSOLE`, `CONSOLE_BIND`, `CONSOLE_PORT` | `off`, `127.0.0.1`, `7070` | the local task page |

On boards with an RGB LED 3 (`LED3_R/G/B` defined by the core), the agent light shows busy
(blue blink), attention (yellow) or done (green); on the VENTUNO Q the icons use the LED
matrix. Whether `LED3_*` are defined for a given board isn't verified: if they aren't, the
sketch compiles without the light and refuses `set_light`.

## Board notes and what isn't verified

Checked against Arduino's source and examples (`app-bricks-py`, `Arduino_RouterBridge`
0.4.x, `ArduinoCore-zephyr`), but **not yet run on a board**:

- **App Lab's runtime:** the Python side runs in its own container and network namespace,
  so `127.0.0.1` there is the container, not the board. The hub and LTMS need addresses
  reachable from the board's LAN.
- **Bridge calls:** `Bridge.provide("mcu_status", ...)` takes the sketch's
  `notify("mcu_status", 4 ints)`. The sketch's `provide_safe("link", int → bool)` runs
  between `loop()` passes. App Lab adds RouterBridge to the sketch by itself (empty
  `libraries:` in `sketch.yaml`).
- **Stopping:** App Lab gives the app 5 seconds to stop, and the member's stop runs inside
  that. If it's cut short, nothing is lost: the outbox is SQLite, and every record is
  committed when it's signed.
- **Reset reason:** reading it needs Zephyr's `hwinfo` driver (`hwinfo_get_reset_cause`). The
  VENTUNO Q variant's Zephyr config doesn't enable `CONFIG_HWINFO`, so the sketch sends `-1`,
  and the record says `"reset_cause": "unknown"`. The Python side already passes a real value
  through once a board provides one.
- **The VENTUNO Q block** uses `ARDUINO_VENTUNO_Q` and `LED2_R/G/B` from the core's
  `arduino_ventuno_q_stm32h5f5xx` variant; its RGB LEDs are active-low (datasheet ABX00181 §5).

## Development

The Python side's logic is in `python/app_member.py` and `python/agent.py`, so it can be tested
without App Lab.
`python/main.py` is the App Lab glue.

```bash
pytest examples/app-lab-member/tests
```
