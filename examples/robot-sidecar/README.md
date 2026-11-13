# Robot sidecar (#312)

**The sidecar never interprets.** It turns each thing a robot (or its voice
bridge) reports into exactly one signed record, with the robot's own values in
the payload. It doesn't summarise, filter for meaning, or invent. The one
exception is body state, folded into a fixed statistical window (min/max/last
every 30 s), which is arithmetic, not judgement.

A robot that can't run the LTMS harness itself joins as a **proxied member**:
this sidecar runs on a nearby host, holds the **robot's** key, and signs as the
robot. It's a member like any other (`cli_agent.member_main`), so it uses the
same outbox, MQTT link to the swarm's hub, and direct fallback. Directing the
robot is not part of it: a separate component can command the robot later, and
the sidecar records what happens like any other event.

## Adapters

| Adapter | Reads | Records |
|---|---|---|
| `wirepod` | a wire-pod server's UI and debug logs (`/api/get_logs`, `/api/get_debug_logs`) | what it heard and the intent matched (`perception`), intent parameters, custom intents and LLM requests (`event`), LLM replies (`communication`) |
| `vector_sdk` | the robot's SDK event stream (`wirepod_vector_sdk`), watching only | wake word, user intents, faces (`event` / `perception`), status flags turning on/off (picked up, held, cliff, charger, charging, falling, button) and touch (`event`), body state (one `internal,telemetry,body` record per window) |

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

## Tests

`pytest examples/robot-sidecar/tests` (also run by the main `pytest`):
each event type is exactly one record of the right type, body state is one
record per window, and the sidecar signs nothing an adapter didn't produce.
