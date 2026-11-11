# Sensing: sensed and perceived

Both **sensed** and **perceived** are `perception` records — the record
contract is frozen, so the two are not two types. The `telemetry` tag selects
*sensed*: a record **is sensed** when its type is `perception` and its tags
include `telemetry`.

## Sensed is data; perceived is an interpretation

**Sensed** is data from a sensor: what the body measured. **Perceived** is an
interpretation of that data: what the agent concluded is true.

> "12 readings from the front time-of-flight sensor, 118 to 131 mm" is
> *sensed*. "Obstacle ahead" is *perceived*.

Send raw measurements as sensed records, and conclusions as plain perception
records. In the xAPI export, sensed records use the `sensed` verb and carry
their payload in the `telemetry` extension; perceived records use
`perceived` and carry their payload in `holocron/payload`.

## Payload convention: any sensor

A sensed record's payload is a JSON object. It is exported whole, whatever
its `kind` — or it has no `kind` at all. The convention:

| Field | Meaning |
|---|---|
| `kind` | what this is: `pose`, `navigation`, `sensor`, or anything the device defines (free) |
| `sensor` | the sensor's name on this device (`front-tof`, `imu`, `battery`, …) |
| `modality` | what it measures: range, imu, temperature, battery, sound-level, light, gps, encoder, force, image-features, … |
| `unit` | a [UCUM](https://ucum.org/) code where one exists (`mm`, `Cel`, `%`, `m/s2`) |
| `rate_hz` | the nominal sample rate in hertz |
| `samples` | a list of `{t, v}`: `t` is seconds from the record's `ts`; `v` may be a number, a list (an IMU's `[ax, ay, az]`) or an object, so any sensor's data element fits |
| `stats` | optional: precomputed `min`, `max`, `mean`, `count`, … |

Pose and navigation records keep their established shapes. No window is
truncated: send the whole window the member kept.

A range sensor (front time-of-flight):

```json
{"kind": "sensor", "sensor": "front-tof", "modality": "range", "unit": "mm", "rate_hz": 10,
 "samples": [{"t": 0.0, "v": 131}, {"t": 0.1, "v": 118}],
 "stats": {"min": 118, "max": 131, "mean": 124.5, "count": 2}}
```

An IMU (one value per axis, as a list):

```json
{"kind": "sensor", "sensor": "imu", "modality": "imu", "unit": "m/s2", "rate_hz": 100,
 "samples": [{"t": 0.0, "v": [0.1, -0.4, 9.8]}, {"t": 0.01, "v": [0.2, -0.3, 9.7]}]}
```

**Send features extracted from images or audio, not the media.** Raw media
(image bytes, audio) is not telemetry; send what the member extracted from it
(`image-features`, `sound-level`), with the media kept on the device.

## How sensing feeds learning

Each step of experience is its own record, and `refs` link them, so a reader
can follow one chain from a sensor reading to a lesson:

| Step | Type | Verb in export | Typical `refs` |
|---|---|---|---|
| Sensor data | perception, tag `telemetry` | sensed | |
| Interpretation | perception | perceived | `derived_from`: the sensed record(s) |
| Reasoning | thought | reasoned | `because`: the perception |
| Commitment | decision | decided | `because`: the perception or thought |
| Act and outcome | action | performed | `because`: the decision |
| Conclusion | lesson | learned | `because`: the outcome(s) |

Worked example (placeholder names; `agent:seq` references the record's owner
and sequence number):

1. `robot-1` senses its front range sensor: perception, tag `telemetry`,
   payload `{"kind": "sensor", "sensor": "front-tof", …}` — **sensed**.
2. It concludes "obstacle ahead": perception,
   `refs: {"derived_from": "robot-1:45"}` — **perceived**.
3. It reasons about passing on the left: thought,
   `refs: {"because": "robot-1:46"}` — **reasoned**.
4. It decides the route: decision, `refs: {"because": "robot-1:47"}` —
   **decided**.
5. It drives: action, `outcome: "success"`,
   `refs: {"because": "robot-1:48"}` — **performed**.
6. It records what worked: lesson, `refs: {"because": "robot-1:49"}` —
   **learned**.

The same chain answers questions at three levels: what one robot learned from
which senses, what a swarm learned when one member's sensing informed another's
decision (references across agents), and how an agent's reasoning used its
body's data.
