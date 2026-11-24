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

The Python side's logic is in `python/app_member.py`, so it can be tested without App Lab.
`python/main.py` is the App Lab glue.

```bash
pytest examples/app-lab-member/tests
```
