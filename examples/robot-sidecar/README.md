# Robot sidecar (#312)

**The sidecar never interprets.** It turns each thing a robot (or its voice
bridge) reports into exactly one signed record, with the robot's own values in
the payload. It doesn't summarise, filter for meaning, or invent. The one
exception is the body window (every 30 s), which is arithmetic, not judgement.
Each window signs:

- one **body-health self-report** (battery volts and level, charging; tags
  `internal,telemetry,body-health`), min/max/last, with `stats.flicker`;
- one **sensed record per sensor group** (tags `telemetry,body`, never
  `internal`; `kind: sensor` with `sensor`, `modality`, `unit`, `samples`,
  `stats`): `wheels`, `imu`, `head`, `lift`, `touch`, `proximity`. Each
  adapter sets per-channel deadbands; an unchanged group is re-sent once per
  `BODY_HEARTBEAT_SECONDS` (default 600 s) as a heartbeat carrying
  `unchanged_since` and `windows_skipped`, so a gap longer than that still
  means silence. While the robot is resting (Vector: on the charger), the
  heartbeat is `BODY_REST_HEARTBEAT_SECONDS` (default 1800 s) instead; changes
  still sign at once (#449);
- the **pose** record (below).

Status flags become **episodes**: one `episode-start` and one `episode-end`
record per happening, tagged with the state (`held`, `cliff`, `falling`,
`on-charger`, `charging`, `button`, `touch`, `animating`, `wheels-moving`).
"Picked up" and "being held" are one state, `held`; the payload keeps the SDK's
raw flag names (`flags`). The end record refs the start
(`refs: {"start": "<agent>:<seq>"}`) and carries `duration_s`. A flip back within
0.5 s is no episode; it's counted in the next window's `stats.flicker`.
`animating` says only *that* an animation is playing: the SDK doesn't say which
animation, sound or light pattern, so the sidecar never names one.

The voice chain is linked: wire-pod's LLM request refs the `heard` perception it
answers, and the reply refs the request (`refs: {"because": ...}`) and carries
`model` and `runs_on` from `SIDECAR_COGNITION_MODELS`.

Pose is sensing, not a self-report: each window's pose goes into its own sensed
record (`kind: pose`, tags `telemetry,pose`, never `internal`), one per frame,
with a sample at most every second and only when the robot moved more than
5 mm or 2° (the window's last sample is always kept). A window in which nothing
moved has no pose record. Vector's odometry restarts when he's picked up, so
each pose origin is its own frame (`vector-odom:<origin id>`), never joined to
the last. The dashboard draws these under **Choreography**.

A robot that can't run the LTMS harness itself joins as a **proxied member**:
this sidecar runs on a nearby host, holds the **robot's** key, and signs as the
robot. It's a member like any other (`cli_agent.member_main`), so it uses the
same outbox, MQTT link to the swarm's hub, and direct fallback. Directing the
robot is not part of it: a separate component can command the robot later, and
the sidecar records what happens like any other event.

## Adapters

| Adapter | Reads | Records |
|---|---|---|
| `wirepod` | a wire-pod server's UI and debug logs (`/api/get_logs`, `/api/get_debug_logs`) | what it heard and the intent matched (`perception`), intent parameters, custom intents and LLM requests (`event`, the request refs the heard), LLM replies (`communication`, refs the request, with `model` and `runs_on`) |
| `vector_sdk` | the robot's SDK event stream (`wirepod_vector_sdk`), watching only | wake word, user intents, faces (`event` / `perception`), episodes of held, cliff, falling, on charger, charging, button, touch, animating and wheels moving (`event` start/end), pose (one sensed `telemetry,pose` record per window and frame, `kind: pose`, frame `vector-odom:<origin id>`), one sensed `telemetry,body` record per sensor group per window, one `internal,telemetry,body-health` self-report per window; on connect, the spec sheet (`report_specs`: board, SDK and firmware versions, animation and trigger **counts**) |

**A frozen SDK stream (`vector_sdk`, #486).** The SDK connection can stop delivering new state without any error. On Vector this happened when another SDK client (VectorClaw, through Hermes) took control. The sidecar kept sampling the last values, the deadbands saw no change, and hours of a moving robot looked like rest. A live Vector never reports **bit-identical** IMU, touch `raw` and proximity `signal_quality` for long: they jitter even on the charger. So when all of them stay exactly the same for `VECTOR_STALE_SECONDS` (default 10; `0` turns it off), the adapter does three things:
- signs one `event` **`body stream stale`** (tags `body,stream-stale`, with `unchanged_s`);
- drops the connection and reconnects 2 s later;
- on reconnecting, signs **`body stream resumed`** (`outage_s`).

While stale, and whenever the last charger reading is more than three battery polls old, Vector isn't treated as resting, so a frozen stream can't hide behind the 30-minute resting heartbeat. Gaps now show on the Timeline as a stale/resumed pair instead of looking like calm.

Each adapter's module docstring lists the exact source lines and SDK names it
was written against (wire-pod commit `dc6ae0c`, `wirepod_vector_sdk` 0.8.1),
so a change upstream can be checked.

What isn't available, and so isn't recorded:
- **What a custom intent's Lua makes the robot say.** wire-pod doesn't log
  `sayText`. A custom intent named in `SIDECAR_STATUS_REPORT_INTENT` is tagged
  `status-report` and, with `SIDECAR_HEALTH_STATE` set, carries the time of the
  hub health reading it would have spoken (`health_reading_at`, see
  [docs/hub-health.md](../../docs/hub-health.md)), so the two can be matched.
  Numbers are never parsed from speech.
- **The robot's stimulation level.** The SDK doesn't expose it.

## Setup

1. On the host, as the service user: create the robot's key and enroll it as
   `kind robot`, proxied by this host:
   ```bash
   python -m cli_agent genkey --name <robot> --out /etc/<sidecar>/<robot>.key   # mode 0600
   python -m cli_agent enroll --server https://ltms.example.org --key /etc/<sidecar>/<robot>.key \
     --kind robot --proxied-by "sidecar on <host>" --swarm <your-swarm>
   ```
   Approve it in the dashboard (compare the fingerprint), then add it to the swarm.
2. For `vector_sdk`: `pip install wirepod_vector_sdk`, then configure the SDK
   for the robot once (`python -m anki_vector.configure`, as the service user).
3. Copy `sidecar.env.example` to `sidecar.env`, fill it in, and install
   `deploy/robot-sidecar.service`.

On start the sidecar reports cognition `offboard` (with the bridge's models
from `SIDECAR_COGNITION_MODELS`), so the profile shows the robot as **Hybrid**.
Set `SIDECAR_COGNITION_MODELS` to the model wire-pod answers with (for example
`<ollama model>@<hub host>`); left empty, the sidecar logs a warning and the
profile shows "Language models: not reported".

## Tests

`pytest examples/robot-sidecar/tests` (also run by the main `pytest`):
each event type is exactly one record of the right type, body state is one
window is one health self-report plus one sensed record per sensor group,
pose is one sensed record per window and frame, a pickup is one episode (start
and end, linked, with duration), a flicker is no episode, the voice chain is
linked, and the sidecar signs nothing an adapter didn't produce.
