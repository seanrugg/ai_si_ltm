# Z3-R0 Build Plan

**Version:** 2.1 · 12 Oct 2026
**Owner:** Sean
**Companion documents:** [Z3-R0 build model](https://claude.ai/artifact/7AWvT8e8UzJa4VBwpwoHvE) (3D model, wiring schedule, parts list) · [Z3-R0 Assembly Guide](https://claude.ai/code/artifact/b2ffd19b-8c88-4846-b0e1-c6b6805cf969) (step-by-step build for anyone making a clone) · [Robot Mesh and Long-Term Memory Plan](https://claude.ai/code/artifact/2850a3ff-4f60-422f-808d-a088a940add1) (the mesh, the memory server and the dashboard)

---

## 1. Summary

Z3-R0 is a two-deck 4WD rover built on an Arduino UNO Q. It is a peer companion for an Anki Vector robot: the two exchange messages as equals, and neither controls the other. Z3-R0 drives on skid-steer, senses obstacles across its front arc and behind it, sees through a USB webcam and a Pi Camera, speaks and listens through USB audio, and shows its mood on an OLED face.

Every communication, decision and action Z3-R0 makes is recorded. Z3-R0 keeps its own local journal, the robot mesh holds the governing record for all agents, and the long-term memory server keeps the history that any mesh member can consult. An on-board Pi Zero W reads that long-term memory and relays the relevant records to the UNO Q, so Z3-R0 can learn from history beyond its own short-term journal.

**Status:** design complete; chassis and power parts arrive 17 Oct 2026. The OLED, webcam and Pi Zero camera can be bench-tested now.

**What changed in version 2.1**

- The left and right distance sensors move to the **front corners, angled 40° outward**. With the center front sensor they cover the forward arc, including the corners that swing out during spin turns.
- The **Pi Zero W returns on board** with two jobs: a second eye (the Pi Camera, streamed to the Jetson) and a **long-term memory relay** that reads and filters the memory server's logs for the UNO Q. It holds no memory of its own.
- The Pi Zero connects to the 4-port hub by one USB cable in **gadget mode**, which powers it and gives it a private wired link to the Q.
- Every action record now carries an **outcome**, so "don't make the same mistake twice" is a simple query.
- A **pan-tilt camera head** is planned as version 2.

**What changed in version 2.0**

- The AIY Vision Bonnet is out. Its vision chip (Myriad) no longer boots, confirmed on two OS images.
- The MQTT broker and governance log moved from Z3-R0 to the Jetson. Z3-R0 keeps a local journal with store-and-forward.
- A governance and audit design (section 9) became a core requirement.

---

## 2. Goals and non-goals

**Goals**

1. A mobile body that drives safely indoors, with obstacle stops that work even if Linux is busy or crashed.
2. Peer interaction with Vector over the robot mesh, with neither robot in charge.
3. A complete, tamper-evident record of what Z3-R0 communicated, decided and did, kept on the robot and on the mesh, and viewable remotely.
4. Decisions informed by history: Z3-R0 consults long-term memory before deliberate actions.
5. A build that someone else can reproduce from the assembly guide and the model.
6. Room to grow: ROS-compatible naming, a pan-tilt head, and a Jetson for heavy perception.

**Non-goals for this version**

- Mapping and room-to-room navigation (needs lidar or depth sensing; see section 13).
- ROS 2 on the robot. Topic and field names stay ROS-compatible so a bridge on the Jetson can add it later.
- Any remote *control* path from the internet. The memory server and dashboard are read and record only.

---

## 3. System architecture

```
                   Long-term memory server  (internet-hosted or home LAN)
                   records · query API · dashboard for the administrator
                      ▲  push records              │  filtered reads (HTTPS)
                      │                            ▼
   ┌──────────────────┴──────────────┐      ┌───────────────────────────┐
   │ Jetson Orin Nano Super          │      │ Pi Zero W  (on Z3-R0)     │
   │  • MQTT broker (robot mesh)     │◄─────┤  • Pi Camera stream       │
   │  • Mesh governance log          │video │  • Long-term memory relay │
   │  • Vector's mind + wire-pod     │      └─────────────▲─────────────┘
   │  • Heavy perception             │                    │ USB (gadget mode)
   └──────▲──────────────────▲───────┘      ┌─────────────┴─────────────┐
          │ Vector SDK       │ MQTT         │ UNO Q  (Z3-R0's brain)    │
   ┌──────┴─────┐            └──────────────┤  Linux: behaviors, voice, │
   │ Vector     │                           │   webcam, local journal   │
   └────────────┘                           │  MCU: motors, sensors,    │
                                            │   reflexes, OLED face     │
                                            └───────────────────────────┘
```

**Roles**

| Node | Role | Source of truth for |
|---|---|---|
| UNO Q | Z3-R0's brain; local journal with store-and-forward | Its own records (short-term memory), until the mesh acknowledges them |
| Pi Zero W (on Z3-R0) | Second eye; long-term memory relay. Stateless: reads, filters, relays | Nothing; it holds no records |
| Jetson (robot mesh) | MQTT broker, governance log, replicator, Vector's mind | All agents on the mesh |
| Memory server | Long-term memory for every mesh member; query API; dashboard | The long-term history (a replica of the mesh log plus direct contributions) |
| Administrator | Reads and filters logs; enrolls agents; owns retention and policy | Decisions about the mesh |

When the Jetson is off, Z3-R0 keeps running, journals locally and catches the mesh up later. When the internet is down, Z3-R0 loses long-term recall but keeps its short-term journal and all safety behaviors.

---

## 4. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Robot computer | Arduino UNO Q, 4 GB / 32 GB (ABX00173) | Linux and a real-time MCU on one board; 32 GB eMMC holds the local journal |
| D2 | Drivetrain | True 4WD skid-steer, two TB6612FNG drivers, every motor on its own channel | All four wheels drive forward and back; steering by side speed |
| D3 | Power | 2S 18650 pack, 5 A fuse, XL4015 buck at 5.1 V into the Q's 5V pin | The Q needs a regulated 5 V supply able to deliver 3 A; VIN needs 7–24 V and a 2S pack sags below 7 V |
| D4 | Obstacle sensing | Four Modulino Distance sensors on a Modulino Hub: front, front-left and front-right (angled 40° out), rear | Covers the forward arc where nearly all collisions happen, including corners during turns; side-mounted sensors rarely prevent a collision on a robot that can't move sideways |
| D5 | Vision, primary | USB webcam on the head mast, processed on the Q | Plug-and-play, works with no network |
| D6 | Vision, second eye | Pi Camera v2.1 on the Pi Zero W, H.264 stream to the Jetson | Sharper optics and heavier recognition without video load on the Q; optional for clone builders |
| D7 | Broker and governance log | On the Jetson | Stationary and always on the LAN; Z3-R0 is battery-powered and goes offline |
| D8 | Long-term memory relay | Pi Zero W reads and filters the memory server and relays records to the Q; holds nothing itself | Gives the Q history beyond its short-term journal without loading it |
| D9 | Pi Zero link | One USB cable from the 4-port hub to the Pi Zero's USB data port, gadget mode | Powers it and gives a private wired link to the Q; no buck tap, no Wi-Fi between them |
| D10 | Remote viewing | Memory server dashboard, login required | Review from anywhere; see the mesh plan |
| D11 | Left driver direction pins | A2–A5, leaving A0/A1 free | A0/A1 are the Q's only DAC outputs |
| D12 | Driver standby | 10 kΩ pull-down on STBY, enabled by D4 | Motors stay off during the ~20 s Linux boot |
| D13 | Action outcomes | Every action record carries an outcome | Lets the Q ask "how did this go last time?" |
| D14 | Pan-tilt head | Version 2: Pi Camera on two micro servos, PCA9685 driver on `Wire`, own 5 V supply | The Pi Camera is small and light enough to move; frees the head from the PWM pin shortage |
| D15 | ROS | Deferred; ROS-style names now | ROS pays off with mapping, which this build doesn't have yet |

---

## 5. Hardware

### 5.1 Subsystems

| Subsystem | Parts | Lives on |
|---|---|---|
| Compute | UNO Q 4 GB | Upper deck, center |
| Drive | 4 TT gear motors, 4 wheels, 2 TB6612FNG, 2 LM393 speed sensors on the rear wheels | Lower deck |
| Power | 2× 18650 in a switched holder, 5 A inline fuse, XL4015 buck with voltmeter | Lower deck |
| Sensing | Modulino Movement (IMU), Light, Buzzer, Hub, 4× Distance | Upper deck (IMU, Light, Buzzer); lower deck front, front corners and rear (Hub, Distance) |
| Face | 0.96" SSD1306 I²C OLED, 128×64 | Head mast, middle |
| Vision | USB webcam (UVC); Pi Camera v2.1 on the Pi Zero W | Webcam on top of the mast; Pi Camera on the mast between webcam and OLED |
| Memory relay | Pi Zero W | Upper deck, in front of the Q |
| Audio | USB speaker, USB mini mic | Upper deck, rear and right edge |
| USB | UGREEN Revodok Pro (USB-C hub with HDMI), Anker 4-port USB hub | Upper deck, left and right edges |

### 5.2 Bill of materials

Status is for Sean's build. Clone builders can substitute any part marked "or equivalent", and can leave out the Pi Zero and Pi Camera for a webcam-only build.

| Part | Qty | Status | Notes for clone builders |
|---|---|---|---|
| Arduino UNO Q, 4 GB (ABX00173) | 1 | Have | The 2 GB model works, with less room for the journal and local models |
| 4WD acrylic chassis kit (YonPhsy or equivalent): 2 decks, 4 TT motors, wheels, encoder discs, standoffs | 1 | Arrives 17 Oct | Any two-deck 4WD TT-motor kit |
| TB6612FNG dual motor driver breakout | 2 | Have | SparkFun or equivalent |
| LM393 slotted speed sensor | 2 | Arrives 17 Oct | Sold in 5-packs |
| 18650 Li-ion cells, button-top | 2 | Arrives 17 Oct | Matched pair from a reputable brand |
| 2×18650 holder with switch | 1 | Arrives 17 Oct | Must be wired in series (7.4 V) |
| Inline ATC blade fuse holder + 5 A fuse | 1 | Arrives 17 Oct | |
| XL4015 5 A buck converter with voltmeter | 1 | Arrives 17 Oct | Must sustain about 3 A continuous |
| 18650 charger | 1 | Arrives 17 Oct | Charge outside the robot |
| Modulino Movement | 1 | Arrives 17 Oct | |
| Modulino Light | 1 | Arrives 17 Oct | |
| Modulino Buzzer | 1 | Arrives 17 Oct | |
| Modulino Hub | 1 | Ordered | Required for more than one distance sensor (they share an address) |
| Modulino Distance | 4 | Ordered | Front, front-left, front-right, rear |
| Small angle brackets (or 3D-printed 40° wedges) | 2 | Needed | Hold the corner sensors at 40° |
| 0.96" I²C OLED (SSD1306), 128×64 | 1 | Have | Check pin order: some boards are VCC-GND, others GND-VCC |
| USB webcam (Microsoft, UVC) | 1 | Have | Any UVC webcam; smaller is better on the mast |
| Raspberry Pi Zero W | 1 | Have | Pi Zero 2 W is a drop-in upgrade with four faster cores |
| Pi Camera v2.1 + 15-to-22-pin Pi Zero camera cable | 1 | Have | Cable came with the AIY kit |
| USB-A to micro-USB **data** cable, short | 1 | Needed | Hub to the Pi Zero's USB data port; charge-only cables won't work |
| 8 GB+ microSD card | 1 | Have | For the Pi Zero |
| USB speaker with built-in sound card (Gikfun or equivalent) | 1 | Arrives 17 Oct | |
| USB mini microphone | 1 | Arrives 17 Oct | |
| USB-C hub with HDMI and USB-C data port (UGREEN Revodok Pro) | 1 | Have | Needs a USB-C data port for the second hub |
| 4-port USB hub with USB-C plug (Anker) | 1 | Have | Speaker, mic, webcam and Pi Zero: all four ports used |
| Qwiic cables (assorted, one long) | 5–6 | Arrives 17 Oct | One long cable reaches the rear sensor |
| 10 kΩ resistor | 1 | Needed | STBY pull-down |
| Lever connectors (Wago 221-style) | 2 | Needed | Power and ground junctions |
| Nylon standoffs, M2.5 and M3 assortment | 1 set | Needed | Board mounting; the Pi Zero takes M2.5 |
| Jumper wires, 20–22 AWG wire, zip ties, Velcro, foam tape | — | Have | |
| Multimeter, soldering iron, wire strippers | — | Have | |

**Version 2 parts (pan-tilt head, not yet ordered):** pan-tilt bracket for micro servos, 2 micro servos (SG90 or MG90S), PCA9685 16-channel servo board, a second small buck converter set to 5 V for the servos, 100 µF capacitor.

**Parts bin after the build:** 1 TB6612, 3 LM393 sensors, 4 battery holders, 1 mic, the kit's 4×AA box, the AIY Vision Bonnet (failed), and the ESP32 with TP4056 and MT3608 boards for a future handheld remote.

---

## 6. Power design

```
2×18650 (7.4 V nom, 8.4 V full) ─ switch ─ 5 A fuse ─┬─► TB6612 right VM ─► right motors
                                                      ├─► TB6612 left VM  ─► left motors
                                                      └─► XL4015 buck IN
                                                            │ 5.1 V
                                                            ▼
                                                     UNO Q 5V pin
                                                            │ (same net as USB-C VBUS)
                                                            ▼
                         UGREEN hub ─► Anker hub ─► speaker, mic, webcam, Pi Zero
```

**Rules**

- The motors run straight from the pack through the drivers, so motor current never passes through the buck.
- The buck is set to 5.1 V before anything plugs into it. The extra 0.1 V covers wire drop; the Q accepts 4.5–5.5 V.
- The Q's 5V pin shares a net with its USB-C VBUS. Never plug a charger into the Q or the UGREEN's PD input while the buck is connected.
- All grounds tie together: pack, buck, both drivers, the Q.
- Recharge when the buck display reads about 6.6 V input.

**5 V rail budget (planning estimates; confirm with the buck display in Phase 6)**

| Load | Typical | Peak | Basis |
|---|---|---|---|
| UNO Q under load | 1.0 A | 2.0 A | Arduino asks for a supply able to deliver 3 A |
| Webcam | 0.2 A | 0.5 A | USB 2.0 device limit is 0.5 A |
| USB speaker | 0.2 A | 1.0 A | 5 W speaker at full volume |
| Pi Zero W with camera streaming | 0.3 A | 0.5 A | Powered through the hub |
| Mic, hubs, OLED, Modulinos, logic | 0.2 A | 0.3 A | Small loads |
| **Total** | **≈1.9 A** | **≈4.3 A** | Peaks don't all coincide; keep volume moderate |

The XL4015 is rated about 3 A continuous, 5 A peak. Mount it with its heatsink in open air. If the Q resets on loud audio, lower the volume or move the speaker to its own 5 V tap. The version 2 servos must not share this rail.

---

## 7. Pin and bus map

### 7.1 UNO Q header pins

| Q pin | Connects to | Job |
|---|---|---|
| D2 | Left rear LM393 OUT | Left wheel pulses (interrupt) |
| D3 | Right rear LM393 OUT | Right wheel pulses (interrupt) |
| D4 | Both TB6612 STBY (10 kΩ to GND) | HIGH enables the drivers |
| D5 | Right TB6612 PWMA | Right front speed (PWM) |
| D6 | Right TB6612 PWMB | Right rear speed (PWM) |
| D7 | Right TB6612 AIN1 | Right front direction |
| D8 | Right TB6612 AIN2 | Right front direction |
| D9 | Left TB6612 PWMA | Left front speed (PWM) |
| D10 | Left TB6612 PWMB | Left rear speed (PWM) |
| D12 | Right TB6612 BIN1 | Right rear direction |
| D13 | Right TB6612 BIN2 | Right rear direction |
| A4 (D18) | Left TB6612 AIN1 | Left front direction |
| A5 (D19) | Left TB6612 AIN2 | Left front direction |
| A2 | Left TB6612 BIN1 | Left rear direction |
| A3 | Left TB6612 BIN2 | Left rear direction |
| SDA / SCL (D20 / D21) | OLED SDA / SCL | Face, on `Wire` |
| 3V3 | TB6612 VCC (both), LM393 VCC (both), OLED VCC | 3.3 V logic |
| GND | All logic grounds | Common ground |
| 5V | Buck OUT+ | Main supply |

**Kept free:** D11 (PWM; spare), A0 and A1 (the only DAC outputs), D0/D1 (UART). The version 2 pan-tilt head uses the PCA9685 on `Wire`, so it needs no extra pins.

PWM on the UNO Q is fixed at 500 Hz, so expect a faint motor whine at low speed.

### 7.2 I²C buses

| Bus (sketch name) | Connector | Devices |
|---|---|---|
| `Wire` | Header SDA/SCL (D20/D21) | OLED at 0x3C; version 2 PCA9685 at 0x40 |
| `Wire1` | Qwiic port (3.3 V only) | Movement → Light → Buzzer → Hub input |
| Hub channels | Modulino Hub | 1 front · 2 front-left · 3 front-right · 4 rear Distance |

Run an I²C scan on each bus in Phase 6 and record the addresses found.

### 7.3 USB

| Device | Plugs into |
|---|---|
| UGREEN hub (USB-C cable) | Q USB-C |
| Anker hub (USB-C plug) | UGREEN USB-C data port |
| Speaker, mic, webcam | Anker USB-A ports |
| Pi Zero W (its **USB** data port, not PWR IN) | Fourth Anker USB-A port, data cable |
| Monitor, keyboard, mouse (desk mode) | UGREEN HDMI and free ports; battery off if desk power is used |

---

## 8. Software architecture

### 8.1 Microcontroller (STM32, Arduino sketch)

The MCU owns everything that must not wait on Linux.

- Motor control: PWM and direction for four channels; STBY via D4.
- Odometry: interrupt counts on D2/D3.
- **Reflex layer:** stop the motors when any Distance sensor reads under the stop threshold, regardless of what Linux asked for. Corner sensors use a longer threshold during spin turns.
- **Command watchdog:** if no drive command arrives from Linux for 500 ms, stop the motors.
- IMU reading for heading correction, bump and pick-up detection.
- OLED face animation and Buzzer chirps.
- Reports sensor events and reflex stops up to Linux so they get journaled.

### 8.2 UNO Q Linux side (App Lab App, Python)

- Behaviors and personality.
- MQTT client to the mesh, with local journal and store-and-forward (section 9).
- Webcam capture and light local detection (faces, motion).
- Voice: Piper text-to-speech to the USB speaker; keyword spotting on the USB mic.
- Before deliberate actions, asks the Pi Zero for long-term context (section 9.6).
- Set to **Run at startup** in App Lab.

The App Lab Bridge connects the MCU and Linux sides.

### 8.3 Pi Zero W (Raspberry Pi OS Lite)

- **USB gadget mode:** appears to the Q as a small network device on a private link.
- **Camera service:** streams hardware-encoded H.264 (720p, 15–30 fps) over Wi-Fi to the Jetson.
- **Memory relay service:** a small local API on the USB link. The Q sends a context request; the Pi Zero queries the memory server over HTTPS with read-only credentials, filters the results and returns a capped list of records. Nothing is stored on the Pi Zero.

### 8.4 Topics

| Topic | Use |
|---|---|
| `robots/z3-r0/out` | What Z3-R0 says or reports to peers |
| `robots/vector/out` | What Vector says or reports |
| `robots/z3-r0/journal` | Z3-R0's journal records (section 9) |
| `robots/z3-r0/ack` | Mesh acknowledgments back to Z3-R0 |
| `robots/z3-r0/vision` | Detections the Jetson publishes from the Pi Camera stream |
| `z3r0/cmd_vel`, `z3r0/range/front` … `z3r0/odom` | ROS-style motion and sensor topics, SI units (m, rad, m/s) |

Each robot gets its own broker login and an ACL that lets it publish only under its own name.

---

## 9. Governance, audit trail and memory

Autonomous machines should keep a record of what they communicated, decided and did, and that record must also live beyond the reach of the machine that wrote it. This design follows the principles of the AI Constitution project. The mesh-wide design (identity, the memory server, the dashboard, what else to capture) is in the Robot Mesh and Long-Term Memory Plan.

### 9.1 Tiers

| Tier | Where | Role | Write access |
|---|---|---|---|
| 1. Local journal | UNO Q, SQLite on eMMC | Z3-R0's own source of truth and short-term memory; survives network loss | Z3-R0 only |
| 2. Mesh governance log | Jetson | Authoritative record for every agent on the mesh | Mesh logger only; agents publish, never edit |
| 3. Long-term memory | Memory server | History every mesh member can query; the administrator's dashboard | Verified ingest only; no edits |

### 9.2 The three core record types

| Type | Example |
|---|---|
| `communication` | Z3-R0 said "Hi Vector, found you by the couch." |
| `decision` | Chose to approach Vector; options were approach, wait, explore; reason: Vector seen at 1.2 m, battery 78% |
| `action` | Drove forward 0.8 m at 0.15 m/s toward Vector; **outcome:** stopped by front reflex at 0.25 m |

Supporting types: `event` (front distance under threshold, picked up, low battery), `perception` (labels of what was seen) and `system` (boot, shutdown, software version, journal purge, connection lost or restored). The mesh plan also defines `lesson` and `policy` records.

### 9.3 Record format

Z3-R0 uses the mesh-wide record envelope defined in the Robot Mesh and Long-Term Memory Plan. Type-specific fields go in `body`.

```json
{
  "v": 1,
  "agent": "z3-r0",
  "seq": 18423,
  "ts": "2026-10-20T19:04:14.902-04:00",
  "type": "action",
  "summary": "Approach Vector",
  "refs": {"decision": "z3-r0:18422"},
  "place": "living-room",
  "peers": ["vector"],
  "tags": ["social"],
  "body": {"distance_m": 0.8, "speed_mps": 0.15,
           "outcome": "reflex_stop", "outcome_detail": "front range 0.25 m"},
  "sw": "z3r0-app 0.5.0",
  "prev_hash": "4ab7…",
  "hash": "c21e…",
  "sig": "9f03…"
}
```

- `seq` increases by one per record and never repeats.
- `hash` is SHA-256 over the record without `hash` and `sig`, including `prev_hash`. Editing or removing any record breaks the chain from that point on.
- `sig` is Z3-R0's Ed25519 signature over `hash`; the key never leaves the UNO Q.
- `outcome` (in `body`, on actions) is one of `success`, `partial`, `reflex_stop`, `stuck`, `abandoned`, `failed`. An action logged before it finishes gets a follow-up record with the outcome, linked through `refs`.
- `place` and `peers` make history searchable by where and with whom.
- Z3-R0 also writes `perception` records (labels such as "Vector, 1.2 m", never images) and `event` records; the mesh plan lists what else is worth keeping.

### 9.4 Flow: write locally first, then forward

1. Z3-R0 writes the record to its local journal **before** acting on or publishing it.
2. It publishes the record to `robots/z3-r0/journal`.
3. The mesh logger verifies the hash chain, stores the record, and returns `ack` with the highest contiguous `seq` received.
4. Z3-R0 marks everything up to that `seq` as acknowledged.
5. If the mesh is unreachable, records queue locally. On reconnect, Z3-R0 replays from the last acknowledged `seq`.
6. The mesh pushes records on to the memory server.

### 9.5 Local storage and cleansing routine (UNO Q)

The journal lives on the Q's 32 GB eMMC, kept to a capped budget so it never crowds out the OS.

| Rule | Default (to confirm) |
|---|---|
| Journal size cap | 2 GB |
| Keep acknowledged records locally for | 30 days |
| Records never purged | Anything not yet acknowledged by the mesh |
| Purge order | Oldest acknowledged first |
| Schedule | Nightly, and whenever the cap is reached |
| Chain continuity | Keep a checkpoint row with the last purged `seq` and `hash`, so the remaining chain still verifies |
| Database upkeep | SQLite WAL mode; weekly `VACUUM` |
| Audit of the purge | Each purge writes a `system` record (range purged, checkpoint hash) that is itself forwarded to the mesh |

If unacknowledged records alone approach the cap (a long mesh outage), Z3-R0 raises a low-storage `event`, shows it on the OLED, and stops recording low-value telemetry before dropping anything else.

### 9.6 Long-term recall through the Pi Zero

- **Short-term memory** is the Q's own journal: recent events, including those not yet replicated.
- **Long-term memory** is the memory server, reached through the Pi Zero.
- Before a deliberate action, the Q sends the Pi Zero a context request: the intended action, place and peers. The Pi Zero asks the memory server for matching history, for example "the last 10 `action` records for *approach Vector* in the living room, with outcomes" or "Vector's communications in the last day", and returns a capped, filtered list.
- The Q weighs that history. If the same action has ended in a reflex stop three of the last four times, it can choose differently.
- The Pi Zero retrieves; it doesn't interpret. Making sense of what Vector meant stays with the Q or the Jetson.
- Replies take a few hundred milliseconds. Recall is for deliberate decisions only, never reflexes.
- If the Pi Zero or the internet is unavailable, the Q decides on short-term memory alone and journals that recall was unavailable.

### 9.7 Security

- MQTT broker on the LAN only; no port forwarding.
- Separate credentials per agent; ACLs limit each to its own topics.
- The Pi Zero's memory-server credentials are read-only.
- All nodes sync time with NTP, so timestamps line up across the mesh.

---

## 10. Vision

| Version | Vision |
|---|---|
| v1 (now) | **Webcam on the Q** for local detection (faces, motion), working with no network. **Pi Camera on the Pi Zero** streams to the Jetson for heavier recognition (Vector, people); detections come back on `robots/z3-r0/vision`. To follow what it sees, Z3-R0 **turns its whole body**: skid-steer can pivot in place, so this needs no new hardware. |
| v2 | **Pan-tilt head:** the Pi Camera moves to a pan-tilt bracket on two micro servos, driven by a PCA9685 on `Wire` and powered by its own 5 V buck with a 100 µF capacitor. A follow loop through the Jetson adds a few hundred milliseconds: smooth for following someone walking, not snappy. |
| To investigate | The UNO Q has a 4-lane MIPI camera interface on its JMEDIA header. If it supports the Pi Camera's IMX219 sensor through an adapter, a head camera on the Q itself would give fast local tracking. Connector, adapter and driver support are unverified. |

Obstacle avoidance never depends on vision; the distance sensors handle it.

**Not used:** AIY Vision Bonnet (Myriad chip failed to boot on the 2018 and 2021 AIY images).

---

## 11. Build phases

| Phase | Work | Done when |
|---|---|---|
| 0 | Bench prep: UNO Q in App Lab; OLED test sketch; webcam on Linux; Pi Zero flashed, camera tested, gadget mode working | OLED shows eyes; webcam appears as a video device; the Q can reach the Pi Zero over USB |
| 1 | Power bench test: pack, fuse, buck set to 5.1 V | 7.4–8.4 V at the pack; 5.1 V at the buck with no load |
| 2 | Chassis and drivetrain | All four motors spin both ways |
| 3 | Lower deck: power junctions, drivers, speed sensors, Hub, Distance sensors (corners at 40°) | Encoders spin clear; sensors see past the wheels |
| 4 | Upper deck: Q, hubs, Pi Zero, speaker, mic, Modulinos, head mast with webcam, Pi Camera and OLED | Nothing touches the wheels; hubs lift off without unplugging audio |
| 5 | Signal wiring per the pin map | Every wire labeled and connected |
| 6 | Power-up and first tests | Every I²C device answers; wheels turn the right way; obstacle stop works on all four sensors |
| 7 | Software bring-up: MCU sketch with reflexes and watchdog; Linux App; local journal with outcomes | Journal records drive, reflex and boot events with a valid hash chain |
| 8 | Mesh integration on the Jetson: broker, mesh logger, Vector's mind, Pi Camera stream | A "hello" exchange with Vector is logged and acknowledged; detections arrive |
| 9 | Memory server and dashboard; Pi Zero memory relay | The Q receives relevant history for a planned action; the exchange is viewable on the dashboard |
| 10 | Retention routine | A forced purge removes only acknowledged records and the chain still verifies |
| 11 (v2) | Pan-tilt head | The head follows a face across the room |

Phases 0–6 are hardware and are covered step by step in the assembly guide. Phases 7–10 are software; the mesh plan covers the server side.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Buck sags or overheats on combined peaks | 5.1 V setpoint, heatsink in open air, moderate volume, watch the buck display in Phase 6 |
| Motors move during boot | 10 kΩ STBY pull-down; drivers enabled only by the sketch |
| Linux hangs while driving | MCU reflex layer and 500 ms command watchdog |
| Skid-steer drift | IMU heading correction |
| Obstacles off the nose missed | Angled corner sensors cover the forward arc |
| Jetson offline | Z3-R0 journals locally and replays on reconnect; behaviors fall back to local-only |
| Internet or memory server down | Recall unavailable; Q decides on short-term memory and journals the gap |
| Pi Zero brown-out on a busy hub | It's within the hub's budget; if it resets under load, feed it from the buck's micro-USB PWR IN instead |
| Bad advice from history | History informs, never overrides; the Q decides and safety stays on the MCU |
| Journal fills the eMMC | Size cap, purge of acknowledged records only, low-storage alert |
| Tampering with records | Per-agent hash chains verified by the mesh; agents can't edit the mesh log or memory server |
| Exposure of the memory server | See the mesh plan: no public read, enrolled agents only, read-only relay credentials |
| Single-cell or wiring short | 5 A fuse at the pack, charging outside the robot |
| Motor noise in the mic | Move the mic up the mast on a short USB extension; ReSpeaker array later |

---

## 13. Open decisions

1. Journal defaults: 2 GB cap and 30-day local retention, or different numbers?
2. Memory server deployment (internet, home LAN or both) and the administrator's login: see the mesh plan.
3. Whether the Jetson runs around the clock or only during sessions.
4. Which local vision model runs on the UNO Q.
5. Whether to upgrade to a Pi Zero 2 W.
6. Whether the UNO Q's MIPI camera interface can take the Pi Camera (would change the v2 head design).
7. Mapping and navigation later: a lidar or depth camera, with ROS 2 on the Jetson bridged to Z3-R0's ROS-style topics.

---

## 14. Datasheet checks

Checked against the UNO Q datasheet, schematics, user manual and power specification (ABX00162/ABX00173).

- [x] 5V pin: regulated 4.5–5.5 V supply able to deliver at least 3 A; shares a net with USB-C VBUS.
- [x] PWM pins: D3, D5, D6, D9, D10, D11.
- [x] I²C: header SDA/SCL (D20/D21) is `Wire`; Qwiic is `Wire1`, 3.3 V only.
- [x] Interrupts: D2 and D3 are STM32 GPIOs and can trigger interrupts; D3 tolerates 3.6 V at most, fine with 3.3 V sensors.
- [x] Storage: ABX00173 has 4 GB RAM and 32 GB eMMC.
- [ ] MIPI-CSI: 4-lane interface on JMEDIA confirmed in the datasheet; Pi Camera compatibility not yet checked.
