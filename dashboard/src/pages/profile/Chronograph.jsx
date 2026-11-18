// The chronograph (#403, docs/holo-light.md "Chronology"): what a member was
// doing at any moment, as rings of light. One full turn is the selected range,
// 0 at 12 o'clock, time running clockwise.
//
// Data is the JSON of GET /api/admin/profile/episodes (#401):
//   {since, until, duration_s, pulses: [[t, transit_s, type, category, source, summary]],
//    lanes: {mind: [[start, end, state, detail, flags?]], ...},
//    series: {volts: [[t, v]], wheel_mmps: [[t, v]]}, heartbeat: {interval_s, last_t}}
// with every t in seconds from `since`.
//
// The maths (arcs, the state vector, the dance and its echoes, narration and
// the table twin's rows) is pure and exported for tests. <Chronograph> only
// draws it on a canvas.
import { useEffect, useRef } from "react";
import { LIGHT, CAT_SELF, LTMS_CORE, MIND, NEUTRAL, category } from "../../lib/visual.js";
import { drawGlow, heartbeatBpm, heartbeatSwell, withAlpha } from "../../lib/light.js";

const TAU = Math.PI * 2;

// ---------------------------------------------------------------- lanes

/** The rings, inside out, with each state's color (the issue's lane colors). */
export const LANES = [
  { key: "mind", name: "Mind", colors: { thinking: MIND } },
  { key: "voice", name: "Voice", colors: { listening: "#7fb6ff", heard: "#4d86ff", replying: "#ffffff" } },
  { key: "attention", name: "Attention", colors: { known: "#d6ff6b", unknown: "#9b7bff" } },
  { key: "body", name: "Body", colors: { held: "#ff7a59", cliff: "#ffbe3b", falling: "#ff3d57" } },
  { key: "motion", name: "Motion", colors: { nudging: "#1f8f78", driving: category("sense").color } },
  { key: "power", name: "Power", colors: { charger: LTMS_CORE, battery: "#3fd47a" } },
  { key: "behavior", name: "Behavior", colors: {}, dashed: true },
];

// Battery is drawn at 0.35 (the trail rung): it is the background state, not an event.
const STATE_ALPHA = { battery: LIGHT.trail };

/** The polygon's nodes: the first six lanes (Behavior has no axis yet). */
export const NODE_LANES = LANES.slice(0, 6).map((l) => l.key);

/** A lane's color for an episode's state. Behavior's states (none defined yet) are neutral. */
export function laneColor(laneKey, state) {
  const lane = LANES.find((l) => l.key === laneKey);
  if (!lane) return null;
  if (lane.key === "behavior") return NEUTRAL;
  return lane.colors[state] ?? null;
}

/** The node color of a lane: its most active state's color (the last listed). */
export function nodeColor(laneKey) {
  const lane = LANES.find((l) => l.key === laneKey);
  const cs = Object.values(lane?.colors ?? {});
  return cs.length ? cs[cs.length - 1] : NEUTRAL;
}

/** The episodes of a lane that are drawn (they have a color). */
export function drawnEpisodes(data, laneKey) {
  return (data?.lanes?.[laneKey] ?? []).filter((e) => laneColor(laneKey, e[2]));
}

/**
 * Is episode e current at t? start <= t < end; an open episode (still going at
 * the range's end) includes its end.
 */
export function isCurrent(e, t) {
  return e[0] <= t && (t < e[1] || (t === e[1] && (e[0] === e[1] || Boolean(e[4]?.open))));
}

/** The episode of a lane at time t, the newest if they overlap. */
export function stateAt(episodes, t) {
  let found = null;
  for (const e of episodes ?? []) {
    if (e[0] > t) break;
    if (isCurrent(e, t)) found = e;
  }
  return found;
}

/** The last value of a [t, v] series at or before t, or null. */
export function seriesAt(series, t) {
  let v = null;
  for (const p of series ?? []) {
    if (p[0] > t) break;
    v = p[1];
  }
  return v;
}

// ---------------------------------------------------------------- the dial

/** The dial angle of `t` seconds into a range of `duration` seconds: 0 at 12 o'clock, clockwise. */
export function angleAt(t, duration) {
  return -Math.PI / 2 + (duration > 0 ? t / duration : 0) * TAU;
}

const MIN_ARC = 0.012; // radians: an instant still shows

/**
 * How an episode is drawn with the hand at `hand`: the part before the hand is
 * `lit` (past), the part after it `asleep` (future). The live episode (start
 * <= hand < end) is lit up to the hand only. Returns
 * {lit: [a0, a1] | null, asleep: [a0, a1] | null, live}.
 */
export function episodeArcs(ep, hand, duration) {
  const [start, end] = ep;
  const a0 = angleAt(start, duration);
  const a1 = Math.max(angleAt(end, duration), a0 + MIN_ARC);
  if (start > hand) return { lit: null, asleep: [a0, a1], live: false };
  if (!isCurrent(ep, hand)) return { lit: [a0, a1], asleep: null, live: false };
  const ah = Math.max(angleAt(hand, duration), a0 + MIN_ARC);
  return { lit: [a0, ah], asleep: ah < a1 ? [ah, a1] : null, live: true };
}

const SECOND = 1;
const TICK_STEPS = [15 * 60, 30 * 60, 3600, 2 * 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400, 14 * 86400, 30 * 86400]
  .map((s) => s * SECOND);
export const MAX_TICKS = 16;

/** Seconds between clock labels: every 15 min, or the smallest step giving at most MAX_TICKS labels. */
export function tickStep(duration) {
  return TICK_STEPS.find((s) => duration / s <= MAX_TICKS) ?? TICK_STEPS[TICK_STEPS.length - 1];
}

/**
 * Clock labels as seconds from `sinceMs`, on whole steps of the viewer's local
 * time (so 15-minute labels read :00 :15 :30 :45, daily ones midnight).
 */
export function clockTicks(sinceMs, duration, step = tickStep(duration)) {
  const stepMs = step * 1000;
  const offset = -new Date(sinceMs).getTimezoneOffset() * 60_000;
  const first = Math.ceil((sinceMs + offset) / stepMs) * stepMs - offset;
  const out = [];
  for (let ms = first; ms < sinceMs + duration * 1000; ms += stepMs) out.push((ms - sinceMs) / 1000);
  return out;
}

export function tickLabel(ms, step) {
  const d = new Date(ms);
  if (step >= 86400) return d.toLocaleDateString([], { month: "short", day: "numeric" });
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** The viewer's time zone, short ("EST"), for table headers. */
export function zoneName(ms = Date.now()) {
  try {
    const part = new Intl.DateTimeFormat([], { timeZoneName: "short" }).formatToParts(new Date(ms))
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? "local";
  } catch {
    return "local";
  }
}

// ---------------------------------------------------------------- the core polygon

const VOICE = { listening: 0.5, heard: 0.75, replying: 1 };
const BODY = { held: 1, falling: 1, cliff: 0.7 };

/** Ranges of the two series, for normalizing: {speedMax, vMin, vMax}. */
export function seriesRanges(data) {
  const speeds = (data?.series?.wheel_mmps ?? []).map((p) => p[1]);
  const volts = (data?.series?.volts ?? []).map((p) => p[1]);
  return {
    speedMax: speeds.length ? Math.max(...speeds) : 0,
    vMin: volts.length ? Math.min(...volts) : 0,
    vMax: volts.length ? Math.max(...volts) : 0,
  };
}

/**
 * The state vector at t, one value 0..1 per node (NODE_LANES order):
 * mind 0/1; voice listening 0.5, heard 0.75, replying 1; attention unknown
 * 0.5, known 1; body cliff 0.7, held or falling 1 (other states 0.7); speed
 * (the motion episode's wheel speed) ÷ the range's max; volts normalized to
 * the range's min..max (1 when they never changed, 0 with no reading).
 */
export function stateVector(data, t, ranges = seriesRanges(data)) {
  const lane = (k) => stateAt(data?.lanes?.[k], t);
  const m = lane("mind"), v = lane("voice"), a = lane("attention"), b = lane("body"), mo = lane("motion");
  let speed = 0;
  if (mo && ranges.speedMax > 0) speed = Math.min(1, (seriesAt(data.series?.wheel_mmps, mo[1]) ?? 0) / ranges.speedMax);
  let power = 0;
  const volts = seriesAt(data?.series?.volts, t);
  if (volts != null) {
    const span = ranges.vMax - ranges.vMin;
    power = span > 0 ? Math.min(1, Math.max(0, (volts - ranges.vMin) / span)) : 1;
  }
  return [
    m ? 1 : 0,
    v ? VOICE[v[2]] ?? 0.5 : 0,
    a ? (a[2] === "known" ? 1 : 0.5) : 0,
    b ? BODY[b[2]] ?? 0.7 : 0,
    speed,
    power,
  ];
}

/** The angle of node i (its axis), 0 at 12 o'clock. */
export function nodeAxis(i, n = NODE_LANES.length) {
  return -Math.PI / 2 + (i / n) * TAU;
}

/** The radius of a node with value `val` in a core of radius `core`. */
export function nodeRadius(val, core) {
  return core * 0.88 * (0.1 + 0.9 * val);
}

export function polar(cx, cy, angle, r) {
  return [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r];
}

// ---------------------------------------------------------------- the dance

export const LEAP_WALL_S = 1.4;
export const EDGE = 0.94; // a leap lands at 94% of the core radius
export const ECHO_ALPHA = { default: 0.22, motion: 0.10 };

/** Does this episode leap? Power never does (too long to be a moment); Motion only when driving. */
export function leaps(laneKey, state) {
  if (!NODE_LANES.includes(laneKey) || laneKey === "power") return false;
  if (laneKey === "motion") return state === "driving";
  return Boolean(laneColor(laneKey, state));
}

/**
 * Every leap in the data, sorted by start: {node, lane, start, from (the node's
 * value at the episode's start), color}. Compute once per data.
 */
export function leapsOf(data) {
  const ranges = seriesRanges(data);
  const out = [];
  NODE_LANES.forEach((k, node) => {
    for (const e of data?.lanes?.[k] ?? []) {
      if (!leaps(k, e[2])) continue;
      out.push({ node, lane: k, start: e[0], from: stateVector(data, e[0], ranges)[node], color: laneColor(k, e[2]) });
    }
  });
  return out.sort((a, b) => a.start - b.start);
}

/** Leap duration in data seconds: 1.4 s of wall time, × the replay speed in Replay. */
export function leapSeconds(mode, speed) {
  return LEAP_WALL_S * (mode === "live" ? 1 : speed || 1);
}

/** Where a leap is at progress u (0..1): from its vertex out to the edge point and back. */
export function leapPoint(from, to, u) {
  const k = Math.sin(Math.PI * Math.min(1, Math.max(0, u)));
  return [from[0] + (to[0] - from[0]) * k, from[1] + (to[1] - from[1]) * k];
}

/**
 * The dance at the hand, in a core of radius `core` centred at (cx, cy):
 *   echoes  one per leap that started at or before the hand: {from, to, color, alpha}
 *           (deterministic: computed from the episodes up to the hand, so scrubbing
 *           back removes them and a paused view equals a played one);
 *   active  node -> {u, from, to, color}: a leap in flight (none when !animate).
 */
export function danceAt(leapList, hand, duration, geom, { animate = true, leapS = LEAP_WALL_S } = {}) {
  const { cx, cy, core } = geom;
  const echoes = [];
  const active = {};
  for (const l of leapList) {
    if (l.start > hand) break;
    const from = polar(cx, cy, nodeAxis(l.node), nodeRadius(l.from, core));
    const to = polar(cx, cy, angleAt(l.start, duration), core * EDGE);
    echoes.push({ from, to, color: l.color, alpha: ECHO_ALPHA[l.lane] ?? ECHO_ALPHA.default, node: l.node });
    const d = hand - l.start;
    if (animate && d < leapS) active[l.node] = { u: d / leapS, from, to, color: l.color };
  }
  return { echoes, active };
}

// ---------------------------------------------------------------- geometry

/** Sizes for a chronograph drawn in a W × H box. `small` leaves no room for labels. */
export function geometry(W, H, small = false) {
  const R = Math.max(10, Math.min(W, H) / 2 - (small ? 4 : 34));
  const core = R * 0.42;
  const ring = (R - core) / LANES.length;
  return { cx: W / 2, cy: H / 2, R, core, ring, radius: (i) => core + i * ring + ring / 2 };
}

/** The lane under (x, y), or null. */
export function laneAtPoint(x, y, geom) {
  const r = Math.hypot(x - geom.cx, y - geom.cy);
  if (r < geom.core || r >= geom.R) return null;
  return LANES[Math.min(LANES.length - 1, Math.floor((r - geom.core) / geom.ring))]?.key ?? null;
}

// ---------------------------------------------------------------- words

const PHRASES = {
  mind: { thinking: (n) => `${n} asked a language model.` },
  voice: {
    listening: (n) => `${n} heard its name.`,
    heard: (n, d) => (d ? `${n} heard: “${d}”` : `${n} heard someone speak.`),
    replying: (n, d) => (d ? `${n} answered: “${d}”` : `${n} answered.`),
  },
  attention: {
    known: (n, d) => `${n} saw ${d || "someone it knows"}.`,
    unknown: (n) => `${n} saw a face it doesn't know.`,
  },
  body: {
    held: (n) => `Someone picked ${n} up.`,
    cliff: (n) => `${n}'s cliff sensors found an edge.`,
    falling: (n) => `${n} felt itself falling.`,
  },
  motion: { driving: (n) => `${n} started driving.` },
  power: { charger: (n) => `${n} went home to its charger.` },
};

/** The plain-words phrase for an episode's start, or null when it isn't notable. */
export function phraseFor(laneKey, ep, name) {
  const fn = PHRASES[laneKey]?.[ep[2]];
  if (!fn) return null;
  const detail = typeof ep[3] === "string" ? ep[3].slice(0, 70) : "";
  return fn(name, detail);
}

function clockText(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/**
 * The latest notable record at the hand, in plain words: the newest episode
 * start with a phrase; else the newest pulse that isn't a self-report or a
 * sensor reading, by its summary. {text, key} or null.
 */
export function narrationAt(data, hand, name, sinceMs) {
  let best = null;
  for (const l of LANES) {
    for (const e of data?.lanes?.[l.key] ?? []) {
      if (e[0] > hand) break;
      if (e[4]?.open && e[0] === 0) continue; // began before the range
      const text = phraseFor(l.key, e, name);
      if (text && (!best || e[0] >= best.t)) best = { t: e[0], text };
    }
  }
  if (!best) {
    for (const p of data?.pulses ?? []) {
      if (p[0] > hand) break;
      if (p[3] === "self" || p[3] === "sense" || !p[5]) continue;
      best = { t: p[0], text: p[5] };
    }
  }
  if (!best) return null;
  return { text: `${clockText(sinceMs + best.t * 1000)} · ${best.text}`, key: `${best.t}` };
}

/** "voice state — detail", or the idle words for an empty lane. */
export function stateText(laneKey, ep, data) {
  if (ep) return ep[3] && ep[3] !== ep[2] ? `${ep[2]} — ${ep[3]}` : ep[2];
  if (laneKey === "behavior" && !(data?.lanes?.behavior ?? []).length) return "not logged yet";
  return "idle right now";
}

/** What touching a ring says: the lane, its state at the hand and its episode count. */
export function laneTouchText(laneKey, data, hand) {
  const lane = LANES.find((l) => l.key === laneKey);
  const eps = drawnEpisodes(data, laneKey);
  const ep = stateAt(eps, hand);
  const n = eps.length;
  return `${lane.name} · ${stateText(laneKey, ep, data)} · ${n} episode${n === 1 ? "" : "s"} this range`;
}

// ---------------------------------------------------------------- table twin

/** "State at the hand": one row per lane. */
export function stateRows(data, hand) {
  return LANES.map((l) => {
    const eps = drawnEpisodes(data, l.key);
    const ep = stateAt(eps, hand);
    return { id: l.key, lane: l.name, state: stateText(l.key, ep, data), count: eps.length, live: Boolean(ep) };
  });
}

export const EPISODE_ROWS = 40;

function lengthText(s) {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

/**
 * "Episodes up to the hand": the newest 40 that started at or before the hand,
 * newest first. The live one's length runs to the hand.
 */
export function episodeRows(data, hand, sinceMs, limit = EPISODE_ROWS) {
  const rows = [];
  for (const l of LANES) {
    for (const e of drawnEpisodes(data, l.key)) {
      if (e[0] > hand) continue;
      const end = Math.min(e[1], hand);
      const flags = e[4] ?? {};
      rows.push({
        id: `${l.key}:${e[0]}:${e[2]}:${rows.length}`, t: e[0], lane: l.name, laneKey: l.key, state: e[2],
        start: new Date(sinceMs + e[0] * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" }),
        length: `${flags.estimated ? "about " : ""}${lengthText(Math.max(0, end - e[0]))}`,
        detail: [e[3] && e[3] !== e[2] ? e[3] : "", flags.estimated ? "estimated" : "", flags.open ? "still open" : ""]
          .filter(Boolean).join(" · "),
        live: isCurrent(e, hand),
      });
    }
  }
  return rows.sort((a, b) => b.t - a.t).slice(0, limit);
}

// ---------------------------------------------------------------- drawing

function ledColor() {
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--c-led").trim();
    const parts = raw.split(/\s+/).map(Number);
    if (parts.length === 3 && parts.every(Number.isFinite)) {
      return `#${parts.map((p) => p.toString(16).padStart(2, "0")).join("")}`;
    }
  } catch {
    // no document: fall through
  }
  return "#00e5ff";
}

function arc(ctx, cx, cy, r, a0, a1) {
  ctx.beginPath();
  ctx.arc(cx, cy, r, a0, a1);
  ctx.stroke();
}

/** Is the member's heartbeat alive at the hand (a self-report within 4 usual intervals)? */
export function heartAlive(heartbeat, hand, pulses) {
  let last = null;
  for (const p of pulses ?? []) {
    if (p[0] > hand) break;
    if (p[3] === "self") last = p[0];
  }
  if (last == null) return false;
  return hand - last < 4 * Math.max(heartbeat?.interval_s ?? 30, 15);
}

/** Most record time (s) the hand may move in one frame and still sound its pulses; a bigger jump is a seek. */
export const PASS_MAX_S = 30;

/** The records the hand passed going from `from` to `to` (seconds), self-reports excluded; none on a seek or going back. */
export function pulsesPassed(pulses, from, to, maxJump = PASS_MAX_S) {
  if (from == null || !(to > from) || to - from > maxJump) return [];
  return (pulses ?? []).filter((p) => p[0] > from && p[0] <= to && p[3] !== "self");
}

/** Seconds from `since` of the latest self-report at or before the hand, or null. */
export function lastSelfAt(pulses, hand) {
  let last = null;
  for (const p of pulses ?? []) {
    if (p[0] > hand) break;
    if (p[3] === "self") last = p[0];
  }
  return last;
}

/**
 * One frame of Chronology sound (#428): pulse every record the hand passed,
 * and beat the member's heart (audible). Returns the heartbeat's swell (0..1),
 * which drives the core's swell.
 */
export function chronographSound(engine, { data, member, from, to }) {
  for (const p of pulsesPassed(data?.pulses, from, to)) engine.pulse(p[3]);
  const last = lastSelfAt(data?.pulses, to);
  const hb = engine.heartbeat(member, {
    intervalSec: data?.heartbeat?.interval_s ?? undefined,
    sinceLastSec: last == null ? null : to - last,
  }, { audible: true });
  return hb?.swell ?? 0;
}

/**
 * Draw the chronograph. `opts`: {data, hand, leapList, animate, leapS, small,
 * sinceMs, nowMs (wall, for the heartbeat), swell (0..1, the sound engine's
 * heartbeat; overrides the drawn-only beat)}.
 */
export function drawChronograph(ctx, W, H, opts) {
  const { data, hand, leapList, animate, leapS, small, sinceMs, nowMs } = opts;
  const duration = Math.max(1, data?.duration_s ?? 1);
  const g = geometry(W, H, small);
  const { cx, cy, R, core, ring } = g;
  const led = ledColor();
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "butt";

  // Rings
  LANES.forEach((lane, i) => {
    const r = g.radius(i);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "#0d1520";
    ctx.lineWidth = ring * 0.7;
    if (lane.dashed) ctx.setLineDash([3, 6]);
    arc(ctx, cx, cy, r, 0, TAU);
    ctx.setLineDash([]);
    for (const e of drawnEpisodes(data, lane.key)) {
      const color = laneColor(lane.key, e[2]);
      const lit = STATE_ALPHA[e[2]] ?? LIGHT.lit;
      const { lit: past, asleep, live } = episodeArcs(e, hand, duration);
      ctx.strokeStyle = color;
      ctx.lineWidth = ring * 0.6;
      if (asleep) {
        ctx.globalAlpha = LIGHT.asleep * (lit / LIGHT.lit);
        arc(ctx, cx, cy, r, asleep[0], asleep[1]);
      }
      if (past) {
        ctx.globalAlpha = live ? (e[2] in STATE_ALPHA ? lit + 0.25 : LIGHT.live) : lit;
        if (live) ctx.lineWidth = ring * 0.7;
        arc(ctx, cx, cy, r, past[0], past[1]);
      }
      if (live) {
        const [x, y] = polar(cx, cy, angleAt(hand, duration), r);
        drawGlow(ctx, x, y, color, ring * 1.3, LIGHT.lit);
      }
    }
  });
  ctx.globalAlpha = 1;

  // The core: polygon, the dance and its echo web, clipped to the core's circle.
  const vec = stateVector(data, hand);
  const swell = !animate ? 0 : opts.swell ?? heartbeatSwell(nowMs, beatStart(nowMs, data?.heartbeat));
  const pts = vec.map((val, i) => polar(cx, cy, nodeAxis(i), nodeRadius(val, core) * (1 + 0.04 * swell)));
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, core, 0, TAU);
  ctx.clip();
  const dance = danceAt(leapList, hand, duration, g, { animate, leapS });
  ctx.lineWidth = small ? 0.6 : 0.8;
  for (const e of dance.echoes) {
    ctx.strokeStyle = e.color;
    ctx.globalAlpha = e.alpha;
    ctx.beginPath();
    ctx.moveTo(e.from[0], e.from[1]);
    ctx.lineTo(e.to[0], e.to[1]);
    ctx.stroke();
    if (!small) drawGlow(ctx, e.to[0], e.to[1], e.color, 3, 0.45);
  }
  ctx.globalAlpha = 1;
  ctx.fillStyle = withAlpha(LTMS_CORE, 0.1);
  ctx.strokeStyle = LTMS_CORE;
  ctx.lineWidth = small ? 1 : 1.4;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  NODE_LANES.forEach((k, i) => {
    const leap = dance.active[i];
    if (leap) {
      const p = leapPoint(leap.from, leap.to, leap.u);
      drawGlow(ctx, p[0], p[1], leap.color, small ? 5 : 10, LIGHT.live);
    } else {
      drawGlow(ctx, pts[i][0], pts[i][1], nodeColor(k), small ? 3 : 7, LIGHT.lit);
    }
  });
  ctx.restore();
  ctx.strokeStyle = "#1a2d46";
  ctx.lineWidth = 1;
  arc(ctx, cx, cy, core, 0, TAU);
  const alive = heartAlive(data?.heartbeat, hand, data?.pulses);
  drawGlow(ctx, cx, cy, CAT_SELF, core * 0.32 * (1 + 0.15 * swell),
           alive ? LIGHT.trail * 0.7 + 0.35 * swell : LIGHT.asleep * 0.35);

  // The hand
  const a = angleAt(hand, duration);
  const [hx0, hy0] = polar(cx, cy, a, core);
  const [hx1, hy1] = polar(cx, cy, a, R + (small ? 2 : 6));
  ctx.strokeStyle = led;
  ctx.globalAlpha = LIGHT.lit;
  ctx.lineWidth = small ? 1 : 1.3;
  ctx.beginPath();
  ctx.moveTo(hx0, hy0);
  ctx.lineTo(hx1, hy1);
  ctx.stroke();
  ctx.globalAlpha = 1;
  drawGlow(ctx, hx1, hy1, led, small ? 5 : 9, LIGHT.live);
  ctx.restore();

  if (small) return;
  // Clock labels (DM Mono, viewer's time zone) and lane names
  ctx.save();
  ctx.globalCompositeOperation = "source-over";
  ctx.font = '400 11px "DM Mono", monospace';
  ctx.fillStyle = "#8aa0b8";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const step = tickStep(duration);
  for (const s of clockTicks(sinceMs, duration, step)) {
    const [x, y] = polar(cx, cy, angleAt(s, duration), R + 22);
    ctx.fillText(tickLabel(sinceMs + s * 1000, step), x, y);
  }
  ctx.textAlign = "right";
  ctx.font = '500 10px "DM Sans", sans-serif';
  LANES.forEach((l, i) => {
    ctx.fillStyle = l.key === "behavior" && !(data?.lanes?.behavior ?? []).length ? "#46596b" : "#9fb3c8";
    ctx.fillText(l.name.toUpperCase(), cx - 8, cy - g.radius(i));
  });
  ctx.restore();
}

/** Wall time (ms) of the latest beat start, at the member's heart rate. */
function beatStart(nowMs, heartbeat) {
  const period = 60_000 / heartbeatBpm(heartbeat?.interval_s);
  return nowMs - (((nowMs % period) + period) % period);
}

function getContext(canvas) {
  try {
    return canvas?.getContext?.("2d") ?? null;
  } catch {
    return null;
  }
}

/**
 * The chronograph on a canvas. Props: data (episodes JSON), hand (seconds from
 * `since`), sinceMs, animate, leapS, small, size (px; default: the parent's
 * width), onLane(laneKey) when a ring is clicked. With `soundEngine` (lib/sound.js)
 * and `member`, records pulse as the hand passes them and the member's
 * heartbeat drives the core's swell (#428); without, it is silent (the swarm's
 * small multiples). VizFrame's Sound toggle and Calm view gate the engine.
 */
export function Chronograph({
  data, hand, sinceMs, animate = true, leapS = LEAP_WALL_S, small = false, size, onLane, leapList, soundEngine, member,
}) {
  const canvas = useRef(null);
  const props = useRef(null);
  const passed = useRef(null); // the hand at the last frame that played sound
  props.current = { data, hand, sinceMs, animate, leapS, small, leapList: leapList ?? [] };

  const draw = () => {
    const c = canvas.current;
    const ctx = getContext(c);
    if (!ctx) return;
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    const css = size ?? Math.min(640, Math.max(240, c.parentElement?.clientWidth || 480));
    if (c.width !== Math.round(css * dpr)) {
      c.width = Math.round(css * dpr);
      c.height = Math.round(css * dpr);
      c.style.width = `${css}px`;
      c.style.height = `${css}px`;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    let swell;
    const p = props.current;
    if (soundEngine && member) {
      if (p.animate) swell = chronographSound(soundEngine, { data: p.data, member, from: passed.current, to: p.hand });
      passed.current = p.hand;
    }
    drawChronograph(ctx, css, css, { ...p, nowMs: performance.now(), swell });
  };

  // Redraw on every change of the hand or data; while animating, also every
  // frame, for the heartbeat and leaps in flight between clock ticks.
  useEffect(draw);
  useEffect(() => {
    if (!animate || typeof requestAnimationFrame !== "function") return undefined;
    let id = requestAnimationFrame(function loop() {
      draw();
      id = requestAnimationFrame(loop);
    });
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, size]);
  useEffect(() => (soundEngine && member ? () => soundEngine.forget(member) : undefined), [soundEngine, member]);

  const onClick = (e) => {
    if (!onLane) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const css = rect.width || size || 480;
    const k = laneAtPoint(e.clientX - rect.left, e.clientY - rect.top, geometry(css, css, small));
    if (k) onLane(k);
  };
  return <canvas ref={canvas} className="mx-auto block" onClick={onClick} width={size ?? 480} height={size ?? 480} />;
}
