# LTMS Holo Light

The LTMS dashboard's look, feel and sound. **Holo** gives the surfaces: deep navy ink, a faint 48px grid, cyan accents and condensed headings. **SENTIO** gives the behavior: light in the dark, touch to reveal, soft sound, nothing found disappears. Holo is how LTMS looks at rest. SENTIO is how it moves when data arrives.

One rule holds it together: **every light is data.** A glow, a pulse or a note always stands for a record, a state or a member. Nothing animates for decoration.

## Principles

1. **Light means data.** Each moving light is one signed record. Each steady light is a member, a swarm or a state that exists right now. If you can't say which record or state a light stands for, it doesn't get drawn.
2. **Brightness is meaning.** One ladder, everywhere (opacity tokens):
   - `light-asleep` (0.18): not available, or future time in a replay;
   - `light-trail` (0.35): history, kept as a faint thread;
   - `light-breathing` (0.6, slow 0.45–0.75 cycle): present and waiting;
   - `light-lit` (0.85): past, found, steady;
   - `light-live` (1): happening now.
3. **Nothing found disappears.** A finished replay is a picture of everything that happened: episodes stay on the rings, paths keep a trail, peers stay in the system. Filters dim things to `light-asleep`; they never delete them from view.
4. **Touch reveals.** Touching or focusing any light names it in the narration line and plays its note (when sound is on). Detail opens in place, never on a new page.
5. **Holo frames, void shows.** Pages, panels and tables are Holo: `ink`, `ink-raised`, `ink-line` hairlines, the grid. Visualizations sit inside a `radius-lg` frame on `void` (pure black), so their light reads. A viz never takes over the page and never needs a start screen.
6. **Sound is optional, soft and in time.** Off by default. When on, it is quiet, quantized to a slow beat and pentatonic, with the heartbeat as its base. See *Motion and sound*.
7. **Same data, every view.** Each visualization has a table twin in the same Holo style (*Calm view*). The two are switched by one toggle and show the same numbers.

## Color

- **Surfaces:** `ink` (page), `ink-raised` (panels, tables), `ink-hover`, `ink-line` (hairlines and grid). `void` only inside visualization frames.
- **Text:** `paper`, `paper-muted`, `paper-faint`. All pass 4.5:1 on `ink` and `ink-raised` (the dashboard's `theme.test.js` asserts it). `paper-faint` is 12px and up only.
- **Accent:** `led` is the one interactive color: focus ring, selected tab, primary button, the replay hand. Nothing else is cyan except `role-member`, which aliases it on purpose: members are the live things.
- **Meaning colors never change with theme** (they live in `dashboard/src/lib/visual.js`):
  - roles color rings: `role-hub`, `role-member`, `role-observer`, `role-free`, `role-leader`;
  - categories color lines and lights: `cat-sense`, `cat-act`, `cat-comm`, `cat-learn`. Each always comes with its dash (2 6 · 8 6 · 14 6 · 10 4 2 4), so color is never the only cue.
- **New in Holo Light:**
  - `cat-self`: self-reports (heartbeat, health). They are drawn as the member's heartbeat, not as a stream.
  - `ltms-core`: the LTMS pyramid.
  - `mind`: a model thinking.
  - `relay`: paths and orbits.
- **Status** (`ok`, `warn`, `bad`) is for dots and text, never a block behind text.
- SENTIO's innocence pink is not used. Its job there, the grown-up controls light, is done here by `led`, and pink in LTMS already means leader and learning.

## Type

- **Bebas Neue** (`display-*`): names and section headings, uppercase, 2.5–3px tracking. Never for numbers or sentences.
- **DM Sans** (`body`, `small`, `label`): all UI text. `label` is uppercase 11px with 1.6px tracking, for table headers and tile labels.
- **DM Mono** (`stat`, `data`, `hash`): every number, time, hash and `agent:seq`. Right-align numbers in tables.
- **Cormorant Garamond italic** (`narrate`, `narrate-sm`): the narration line under a visualization, and quiet empty states. Taken from SENTIO, it is the voice that says what just happened ("9:02 PM · Someone picked rover-1 up.") and is used for nothing else.
- Fonts are bundled with `@fontsource`, never loaded from a CDN, so LTMS works on closed networks.

## Shape and space

- **Spacing:** steps of 4px (`space-1` to `space-4`), 24px between panels, and the 48px grid (`space-12`) as the page rhythm.
- **Radii:** `radius-sm` for controls, `radius-md` for panels and tiles, `radius-lg` for visualization frames, `radius-pill` for tab and range pills.
- **Shapes carry identity:**
  - LTMS is a **pyramid**;
  - a swarm is a **star system**: the hub is the star, members are planets, a member's senses are moons;
  - cloud agents are **satellites**;
  - a member's day is a **chronograph** of rings.
- **Lines connect things.** A path of light between two lights means data moved between them. A dashed `relay` line means structure: the data could move there.
- Holo uses hairlines, not shadows. The only glows are `glow-led` (selected or focused) and `glow-live` (a number that just changed).

## Content

- Plain language, present tense. Say what happened, not what the code did: "He heard his name", not "wake_word event received".
- Numbers carry their unit and source: "6.1 s transit (measured)", "about 45 min (estimated)". Anything approximate says so where it is shown.
- Every count says what it counts. A Sensing tile says "sensed and perceived records", and what it leaves out goes on a second line.
- Empty states are narration, not errors: "Nothing has arrived in this range."

## Sections

- **Motion and sound**: timings, the beat clock, the heartbeat.
- **Visualizations**: Geography, Topology and Chronology, and which profile tab each replaces.
- **Calm view and access**: the table twin, keyboard, reduced motion, flash limits.

# Motion and sound

Motion and sound follow the data's rhythm, not their own. Both are slow, soft and predictable. Live data is sparse and replays are dense, so both are held to a beat clock that keeps a burst of records from turning into noise.

## Motion

| Motion | Timing | Means |
|---|---|---|
| Breathe | 3.2 s cycle, opacity 0.45–0.75, ease in-out | Present and waiting (a member online, a swarm with no traffic) |
| Heartbeat | lub-dub: two swells 120 ms apart, then rest; rate below | The member's self-reports are arriving |
| Travel | 0.9–2.5 s along a path; length scales with the record's measured transit time | One record moving from where it was made to LTMS |
| Arrive | 160 ms flash on the target in the record's category color, then a 0.7 s decay | LTMS received the record |
| Trail | the path keeps `light-trail` for the rest of the view | History stays visible |
| Reveal | 600 ms ease-out (cubic) for zooming between levels and opening detail | Moving between Geography, Topology and Chronology |
| Hand | moves with real time (live) or replay time | Now |

**Rules**
- **At most 3 flashes a second** for any one light (WCAG 2.3.1). Arrivals within the same beat merge into one brighter flash.
- **Nothing spins for show.** The globe turns only when the person drags it, or very slowly (0.3°/s) after 20 seconds of no input.
- **With `prefers-reduced-motion`, or in Calm view:**
  - no travel, breathing or rotation;
  - arrivals become a 1-step brightness change;
  - the hand still moves, because it is the data.

## Sound

**Off by default.** Each visualization header has a Sound toggle. Turning it on is the gesture browsers need to unlock audio, so there is no start screen.

**The beat clock**
- Everything is quantized to **60 BPM** (one beat a second of wall time), on an eighth-note grid.
- A record that arrives between ticks waits for the next tick, at most 0.5 s. In replay, the clock runs in wall time, so 60× playback is still one beat a second.
- **One voice per category per beat.** More records in the same beat raise that voice's loudness slightly, up to a cap. They never add notes.

**Voices** (all from one D major pentatonic, so any combination is consonant)

| Voice | What triggers it | Sound | Where it plays |
|---|---|---|---|
| Heartbeat | self-reports (`cat-self`) | Lub-dub: two low sine thumps (≈55 Hz then ≈48 Hz, 120 ms apart, 180 ms decay) with a gentle low-pass | Beats 1 and 3 when the member is reporting. See *Heartbeat* below. |
| Sensing | `cat-sense` arrivals | Soft marimba-like sine, D3–A3 | Downbeats |
| Deciding & acting | `cat-act` arrivals | Rounded bell (sine + quiet 2:1 partial), F#3–B3 | Off-beats |
| Communicating | `cat-comm` arrivals | Clear high sine, A4–D5, longer tail | At most once per bar |
| Learning & rules | `cat-learn` arrivals | Slow pad swell (2 s attack) on the chord root | When a lesson or policy lands; rare by nature |
| Touch | a light touched or focused | One short sine at that object's pitch | Immediately; not quantized |

**Levels**
- The master gain sits around −30 dBFS. Attacks are 30–60 ms (no clicks), and every voice goes through a 2.2 kHz low-pass.
- No square or saw waves, and no "wrong" sounds. A failed outcome plays the same voice, one step lower.
- There is a short room reverb (0.25 s delay, 0.2 feedback), so the sound reads as distant chimes, not alerts.

## Heartbeat

Self-reports are a member's pulse. In the current dashboard they are a number nobody reads ("153 self-reports not counted here"). In Holo Light they are the member's light beating.

- **Rate:** the heartbeat follows the arrival of self-reports, mapped into a resting range of **48–60 BPM**. A member reporting every 30 seconds beats at 48. Faster reporting raises it toward 60 and never higher, so a heartbeat never sounds anxious.
- **Missing beats:**
  - when self-reports stop for longer than twice their usual interval, the beat slows;
  - after four missed intervals it fades to `light-asleep` and goes silent.

  Silence is the signal. There is no alarm sound.
- **Visual:** the member's light swells in time with the lub-dub (scale 1.0 to 1.08, opacity `light-lit` to `light-live`). In Geography it is the swarm's light, the sum of its members' beats.

# Visualizations

Three views replace today's profile tabs. The tabs are renamed **Geography**, **Topology** and **Chronology**; the drawings keep their names (the globe, the star system, the chronograph). Each sits inside a profile, under the profile header and tabs, in a `radius-lg` frame on `void`, with a table twin one toggle away. There is no entry screen: the view is live when the tab opens.

| View | Replaces | Where | Shows |
|---|---|---|---|
| **Geography** (the globe) | Network, and Places & movement | Swarm and member profiles | Where things are and what is flowing to LTMS |
| **Topology** (the star system) | Interactions | Swarm profile (member profile: the member's own orbit) | Who relays for whom, and the signal paths |
| **Chronology** (the chronograph) | Activity | Member profile (swarm profile: one ring set per member, stacked) | What the member was doing at any moment |

## Common frame

- **Header row** (Holo):
  - the view name in `display-md`;
  - the range pills (24h 7d 30d All);
  - Live / Replay, with speed (30× 60× 120× 240×);
  - the Sound toggle;
  - Table / View.
- **Narration line** (`narrate`) under the frame: the latest notable record in plain words. It is announced politely to screen readers.
- **Time bar** at the bottom: the `led` hand on a `led-dim` track. Drag it or use the arrow keys (±1 min). In Live mode the hand sits at now.
- **Legend:** category chips (color, dash and name), role rings, and the brightness ladder, in the same row style as today's legend.

## Geography (replaces Network, and Places & movement)

- An orthographic globe:
  - land as `ink-line` fill with a hairline edge;
  - a faint graticule;
  - the atmosphere a soft `ltms-core` rim.
  - Drag to turn, pinch or scroll to zoom.
- **LTMS is a pyramid** of `ltms-core` light, hovering over the server's place on a thin column. It is the heart: every stream ends there.
- **Swarms are breathing lights** at their home location. Size grows with the log of the record count in range. Members at one address are one light. Zooming in or touching it opens that swarm's System.
- **Streams** arc from each swarm to the pyramid along a great circle lifted above the surface. Each record is one light in its category color, and travel time scales with measured transit.
- **Cloud agents are satellites** on a dashed `relay` orbit, because their location is their provider's, not theirs.
- **Location honesty:** a place comes from GPS, declared, or a network estimate (#346, #388). Network estimates draw as a soft disc of city-level radius, not a point, and say "estimated" on touch. Unplaced members gather in an "Unplaced" ring at the edge of the frame with the reason, as today's list does. When the database is DB-IP, its credit shows in the frame corner.

## Topology (replaces Interactions)

- **The hub is the star** (`role-hub`). A model running on the hub flares it with `mind` while thinking.
- **Members are planets** on elliptical orbits, ringed by role color. Their kind shows in the planet's icon on touch, and the orbit distance is stable (sorted by name), not meaningful.
- **A member's senses are moons**, one per sensor that produced sensed records in range: camera, mics, IMU, cliff, battery. A moon lights when its sensor fires.
- **Signal paths:** moon → planet → star → up to the LTMS pyramid at the top of the frame. A message between members arcs planet to planet in `cat-comm`.
- **Heartbeat:** each planet beats with its self-reports.
- **Members elsewhere** (a cloud agent in this swarm) sit on a dashed outer orbit labelled "elsewhere".

## Chronology (replaces Activity)

- One full turn is the selected range: a session in Replay, the last 24 h in Live.
- **Rings, inside out:** Mind · Voice · Attention · Body · Motion · Power · Behavior. Each ring holds episodes (a start, an end and a state) derived from records when the view is read.
- **Past is `light-lit`, future is `light-asleep`.** The live episode is bright up to the hand only.
- **The core polygon** is the state vector at the hand: one axis per ring, so its shape is the member's state right now.
- Touch a ring to hear its note and get its current state and episode count in the narration line.
- **The sensing-to-learning chain** (Sensed → Perceived → Reasoned → Decided → Performed → Learned) stays above the chronograph as six Holo stat tiles. Their numbers glow `glow-live` for 2 s when they change.

## Data rules for every view

- Positions, orbits and ring order are for reading. Only time, counts and category carry data, and the legend says which.
- Estimated values (a reply's spoken length, a network location) are labelled "estimated" wherever they are shown.
- A view never shows a number the table twin doesn't.

# Calm view and access

Access is part of the theme, not a different theme. **Calm view** is the same Holo page, same fonts and colors, with motion and sound off and the table twin first. It replaces the white high-contrast page from the first mockup, which looked like a different product.

## Calm view

- **Turned on** by the frame's Table toggle, by the system's `prefers-reduced-motion`, or by a per-user setting ("Start visualizations in Calm view").
- **What changes:**
  - sound off;
  - no travel, breathing or rotation;
  - the table twin shows first, the still visualization second.
- **What stays:**
  - every color and the grid;
  - the narration line;
  - the time bar and the hand.

## The table twin (Holo table)

- **Panel and rows:** a panel on `ink-raised` with a `radius-md` corner. Rows are separated by `ink-line` hairlines, with no zebra striping and no white anywhere.
- **Header:** `label` (DM Sans, 11px uppercase, `paper-muted`), one row, not sticky unless the table scrolls.
- **Cells:**
  - names in `body-strong`;
  - numbers in `data` (DM Mono), right-aligned;
  - times in the viewer's time zone with the zone named once in the header ("Start (ET)").
- **Category cells** begin with the category's line sample (color and dash, 20px), then its name. Role cells begin with the role ring.
- **Live row** (what is true at the hand): a small `light-live` dot before the first cell. The row itself doesn't change color.
- **Hover:** `ink-hover`. **Focus:** the `led` ring on the row.
- **One table per question**, each with a `display-md` caption: Members, Records by category, State at the hand, Episodes.

## Keyboard and screen readers

- **Tab** reaches the frame's header controls, then a list of the touchable lights at the current level (each a real button with its name), then the time bar.
- **Enter** touches a light, the same as clicking.
- **Escape** goes up a level (Chronology's ring → Topology's star system → Geography's globe; Escape always goes one level out).
- **Space** pauses or plays a replay.
- **Arrow keys** move the time bar by one minute.
- The narration line is an `aria-live="polite"` region. It speaks at most once every 2 seconds and skips self-reports.
- Each canvas is `aria-hidden`: its meaning is in the buttons, the narration and the table twin, never only in pixels.

## Limits that always hold

- Text contrast is 4.5:1 (3:1 at 24px and up), checked by `theme.test.js`.
- Meaning is never carried by color alone: categories have dashes, roles have rings and labels, and states have names.
- No more than 3 flashes a second (WCAG 2.3.1).
- Sound is never the only channel: everything heard is also shown.
- Touch targets are at least 32px, even when the light drawn is smaller.
