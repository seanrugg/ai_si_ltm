// SystemView: one swarm as a star system, on canvas (#404, docs/holo-light.md "Topology").
// Hubs are stars, members planets, a member's senses moons, LTMS the pyramid.
// Every moving light is one record from `/admin/profile/episodes`.
//
// The Topology tab draws it full-frame. Geography (#405) draws the same system
// on top of its globe: give it a centre, a size, an alpha (0..1, for the zoom
// fade), the pyramid's place on the globe (`ltmsTarget`) and `skipLtms` when the
// globe draws the pyramid itself. Or call `drawSystem` on your own canvas.
//
// Props:
//   scene       from lib/topology.js buildScene (required)
//   frame       VizFrame's frame: {t, mode, speed, animate, clock, narrate} (required)
//   edges       `/profile/graph` edges, for the planet → planet message arcs (optional)
//   cx, cy      the system's centre in CSS px (default: mid-width, 60% down)
//   size        the system's width in CSS px (default: fits the canvas)
//   alpha       0..1, multiplies every light (default 1)
//   ltmsTarget  {x, y} of the pyramid in CSS px (default: top centre)
//   skipLtms    don't draw the pyramid (default false); records still travel to ltmsTarget
//   selected    a touchable id ("planet:rover-1") to ring
//   height      the canvas height in CSS px (default 420); the width follows the stage
//   soundEngine the lib/sound.js engine (default: the shared `sound`). Records
//               pulse as they land on the pyramid; each planet's heartbeat
//               swells its light, and only one is audible (audibleMember).
//               VizFrame's Sound toggle and Calm view gate it (#428).
//
// Sound lives in this component, not in createSystemMotion/drawSystem, because
// Geography reuses those two and plays its own sounds.
import { useEffect, useRef } from "react";
import { drawGlow, heartbeatSwell, pyramid } from "../../lib/light.js";
import { createEventCursor } from "../../lib/replay.js";
import { sound as sharedSound } from "../../lib/sound.js";
import {
  alongRoute, categoryColor, commPeer, createArrivalFlasher, flaringHubs, isMemberMessage, layoutSystem,
  nodePos, pulseRoute, travelMs,
} from "../../lib/topology.js";
import { CAT_SELF, CATEGORIES, LIGHT, LTMS_CORE, MIND, RELAY, ROLES } from "../../lib/visual.js";

const SENSE = CATEGORIES.find((c) => c.key === "sense").color;
const COMM = CATEGORIES.find((c) => c.key === "comm").color;
const MAX_FLIGHTS = 400;
const STEP_MS = 1000; // Calm view: an arrival is one brightness step for a second
const MOON_FLASH_MS = 700;

/** Default geometry for a W × H stage (the prototype's proportions). */
export function defaultGeometry(W, H) {
  return { cx: W / 2, cy: H * 0.6, size: Math.min(W * 1.05, H * 1.35), ltmsTarget: { x: W / 2, y: H * 0.13 } };
}

/** drawGlow with the radius before the color, as read here: glow(ctx, x, y, radius, color, alpha). */
function glow(ctx, x, y, r, color, alpha) {
  drawGlow(ctx, x, y, color, r, alpha);
}

function ellipse(ctx, o, dash) {
  ctx.setLineDash(dash || []);
  ctx.beginPath();
  ctx.ellipse(o.cx, o.cy, Math.max(0.1, o.rx), Math.max(0.1, o.ry), 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
}

function label(ctx, text, x, y, color, alpha, font = '500 12px "DM Sans", sans-serif') {
  ctx.save();
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = alpha;
  ctx.font = font;
  ctx.textAlign = "center";
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.restore();
}

/**
 * Draw one frame of the system. Pure drawing: everything that moves comes in `look`.
 *   look = {nowMs (wall), tHand (ms), still, alpha, focus, selected, skipLtms, ltms: {x, y},
 *           flights: [{points, color, born, life, lift, self}], ltmsFlash: {color, a} | null,
 *           lit: {nodeKey: 0..1} extra brightness, beats: {member: wallMs},
 *           swells: {member: 0..1} | undefined (the sound engine's heartbeat swell; overrides beats)}
 */
export function drawSystem(ctx, scene, layout, look) {
  const { nowMs, tHand, still, alpha = 1, focus, selected, skipLtms, ltms, flights = [], lit = {}, beats = {}, swells } = look;
  const A = (a) => Math.max(0, Math.min(1, a * alpha));
  const dim = (member) => (focus && member !== focus ? LIGHT.asleep / LIGHT.lit : 1);
  const flaring = flaringHubs(scene, tHand);

  ctx.save();
  ctx.lineWidth = 1;
  // Orbits (structure: dashed relay is "could move here").
  ctx.strokeStyle = RELAY;
  for (const name of scene.order) {
    const p = layout.planets[name];
    if (!p || scene.planets[name].elsewhere) continue;
    ctx.globalAlpha = A(0.4 * dim(name));
    ellipse(ctx, p.orbit);
  }
  if (scene.elsewhere.length) {
    ctx.globalAlpha = A(0.4);
    ellipse(ctx, layout.outer, [2, 7]);
    label(ctx, "elsewhere", layout.outer.cx + layout.outer.rx, layout.outer.cy + layout.outer.ry * 0.2 + 16,
          RELAY, A(0.9), '500 11px "DM Sans", sans-serif');
  }
  // Beams: each star to the pyramid.
  for (const h of scene.hubs) {
    const s = layout.stars[h.name];
    const g = ctx.createLinearGradient?.(s.x, s.y, ltms.x, ltms.y);
    if (g) {
      g.addColorStop(0, ROLES.hub.color);
      g.addColorStop(1, LTMS_CORE);
      ctx.strokeStyle = g;
    } else ctx.strokeStyle = LTMS_CORE;
    ctx.globalAlpha = A(0.3);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(ltms.x, ltms.y);
    ctx.stroke();
    ctx.lineWidth = 1;
  }
  // Relay spokes: star to each of its planets.
  ctx.strokeStyle = RELAY;
  for (const h of scene.hubs) {
    const s = layout.stars[h.name];
    for (const name of h.planets) {
      const p = layout.planets[name];
      ctx.globalAlpha = A(0.3 * dim(name));
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }
  }
  ctx.restore();

  if (!skipLtms) {
    const f = look.ltmsFlash;
    if (f) glow(ctx, ltms.x, ltms.y, 34, f.color, A(Math.min(0.7, f.a)));
    ctx.save();
    ctx.globalAlpha = alpha;
    pyramid(ctx, ltms.x, ltms.y, 30, still ? 0 : nowMs / 1000, f ? f.a : (lit.ltms ?? 0));
    ctx.restore();
    label(ctx, "FLTMS", ltms.x, ltms.y - 34, LTMS_CORE, A(1));
    if (selected === "ltms") ring(ctx, ltms.x, ltms.y - 4, 26, LTMS_CORE, A(1));
  }

  // Stars.
  for (const h of scene.hubs) {
    const s = layout.stars[h.name];
    const boost = lit[`star:${h.name}`] ?? 0;
    glow(ctx, s.x, s.y, s.r * (1 + 0.3 * boost), ROLES.hub.color, A(LIGHT.lit + 0.15 * boost));
    if (flaring.has(h.name)) {
      const breath = still ? 1 : 1 + 0.15 * Math.sin(nowMs / 160);
      glow(ctx, s.x, s.y, s.r * 0.9 * breath, MIND, A(LIGHT.live));
    }
    glow(ctx, s.x, s.y, 9, "#ffffff", A(1));
    label(ctx, h.name, s.x, s.y + s.r * 0.7 + 14, ROLES.hub.color, A(1));
    if (selected === `star:${h.name}`) ring(ctx, s.x, s.y, s.r * 0.6, ROLES.hub.color, A(1));
  }

  // Planets and moons.
  for (const name of scene.order) {
    const p = layout.planets[name];
    const info = scene.planets[name];
    const color = (ROLES[info.role] ?? ROLES.free).color;
    const d = dim(name);
    const swell = still ? 0 : swells ? (swells[name] ?? 0) : heartbeatSwell(nowMs, beats[name]);
    const boost = Math.max(lit[`planet:${name}`] ?? 0, swell);
    const base = info.shown ? LIGHT.lit : LIGHT.trail;
    glow(ctx, p.x, p.y, p.r * (1 + 0.08 * swell) * 1.6, info.elsewhere ? LTMS_CORE : color,
             A((base + (LIGHT.live - LIGHT.lit) * boost) * d));
    glow(ctx, p.x, p.y, 5, "#ffffff", A(d));
    ring(ctx, p.x, p.y, p.r * 0.75 * (1 + 0.08 * swell), color, A(0.8 * d));
    if (selected === `planet:${name}`) ring(ctx, p.x, p.y, p.r * 1.2, color, A(1));
    label(ctx, name, p.x, p.y + p.r + 16, info.elsewhere ? RELAY : "#E7EAF0", A(Math.max(0.5, d)));
    for (const src of info.moons) {
      const key = `${name}:${src}`;
      const m = layout.moons[key];
      const on = lit[`moon:${key}`] ?? 0;
      const c = src === "battery" ? CAT_SELF : SENSE;
      glow(ctx, m.x, m.y, m.r * (1.6 + 1.4 * on), c, A((LIGHT.trail + (LIGHT.live - LIGHT.trail) * on) * d));
      if (selected === `moon:${key}`) ring(ctx, m.x, m.y, m.r * 2.2, c, A(1));
    }
  }

  // Travelling records.
  for (const f of flights) {
    const u = Math.min(1, (nowMs - f.born) / f.life);
    const at = alongRoute(f.points, u, f.lift);
    if (at) glow(ctx, at.x, at.y, f.self ? 4 : 8, f.color, A(f.self ? 0.4 : 1));
  }
}

function ring(ctx, x, y, r, color, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(x, y, Math.max(1, r), 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

/**
 * The moving parts: turns records reaching the hand into flights, flashes,
 * steps (Calm view) and heartbeats. Pure enough to test: pass wall times in.
 */
export function createSystemMotion(scene, { edges = [] } = {}) {
  const cursor = createEventCursor(scene.events);
  const flights = [];
  const flasher = createArrivalFlasher(3);
  const steps = {}; // nodeKey → wall ms until which it is a step brighter (Calm view)
  const moonFlash = {}; // moonKey → wall ms of its last firing
  const beats = {};
  const lastSelf = {}; // member → record time (ms) of its latest self-report up to the hand
  let primed = false;
  const catchUp = (t) => {
    for (const k of Object.keys(lastSelf)) delete lastSelf[k];
    for (const e of cursor.upTo(t)) if (e.category === "self") lastSelf[e.member] = e.t;
  };

  function reset() {
    flights.length = 0;
    flasher.reset();
    for (const k of Object.keys(steps)) delete steps[k];
  }

  return {
    flights,
    beats,
    lastSelf,
    flasher,
    /**
     * Advance to the hand. `windowMs`: records further back than this behind the
     * hand land without travelling (a seek or the first frame). Returns the
     * fresh records (for narration). `onLand(category)` is called when a
     * record's light lands on the pyramid (not for self-reports, member-to-member
     * arcs, records landed by a seek, or in Calm view).
     */
    advance(tHand, nowMs, { animate, layout, ltms, windowMs = 3000, onLand }) {
      const { fresh, reset: back } = cursor.advance(tHand);
      if (back) {
        reset();
        catchUp(tHand);
      }
      if (!primed) {
        primed = true;
        catchUp(tHand);
        return [];
      }
      for (const e of fresh) {
        const route = pulseRoute(e, scene);
        const self = e.category === "self";
        const color = categoryColor(e.category);
        if (route[0].startsWith("moon:")) moonFlash[route[0].slice(5)] = nowMs;
        if (self) {
          beats[e.member] = nowMs;
          lastSelf[e.member] = e.t;
        }
        if (tHand - e.t > windowMs) continue;
        if (!animate) {
          // Reduced motion: no travel; the target takes one brightness step.
          steps.ltms = nowMs + STEP_MS;
          steps[route[0]] = nowMs + STEP_MS;
          continue;
        }
        const points = route.map((k) => nodePos(k, layout, ltms)).filter(Boolean);
        if (points.length > 1 && flights.length < MAX_FLIGHTS) {
          flights.push({ points, color, category: e.category, born: nowMs, life: travelMs(e.transit), lift: 12, self });
        } else if (points.length <= 1) {
          flasher.arrive(color, nowMs);
          if (!self) onLand?.(e.category);
        }
        if (isMemberMessage(e) && scene.planets[e.member]) {
          const peer = commPeer(e.member, edges, scene);
          const a = layout.planets[e.member];
          const b = peer && layout.planets[peer];
          if (a && b && flights.length < MAX_FLIGHTS) {
            flights.push({ points: [a, b], color: COMM, born: nowMs, life: travelMs(e.transit), lift: 30, arc: true });
          }
        }
      }
      // Land finished flights on the pyramid.
      for (let i = flights.length - 1; i >= 0; i--) {
        const f = flights[i];
        if (nowMs - f.born >= f.life) {
          flights.splice(i, 1);
          if (!f.self && !f.arc) {
            flasher.arrive(f.color, nowMs);
            onLand?.(f.category);
          }
        }
      }
      return fresh;
    },
    /** Extra brightness per node key at `nowMs` (moons that fired; Calm view steps). */
    lit(nowMs) {
      const out = {};
      for (const [k, until] of Object.entries(steps)) if (nowMs < until) out[k] = 1;
      for (const [k, at] of Object.entries(moonFlash)) {
        const dt = nowMs - at;
        if (dt >= 0 && dt < MOON_FLASH_MS) out[`moon:${k}`] = Math.max(out[`moon:${k}`] ?? 0, 1 - dt / MOON_FLASH_MS);
      }
      return out;
    },
  };
}

/**
 * The one member whose heartbeat is audible: the selected planet (or the
 * planet of a selected moon), else the profile's own member. Hubs' stars are
 * visual only; on a swarm profile with nothing selected, nobody is audible.
 */
export function audibleMember(selected, scene) {
  if (typeof selected === "string") {
    if (selected.startsWith("planet:")) return selected.slice(7);
    if (selected.startsWith("moon:")) return selected.slice(5).split(":")[0];
  }
  return scene?.focus ?? null;
}

/**
 * Drive every planet's heartbeat through the sound engine for one frame.
 * Returns {swells: {member: 0..1}, keys: [member]}: draw each planet with its
 * swell, and forget the keys when the view goes away.
 */
export function systemHeartbeats(engine, scene, motion, { tHand, audible }) {
  const swells = {};
  const keys = [];
  for (const name of scene.order) {
    const last = motion.lastSelf[name];
    const hb = engine.heartbeat(name, {
      intervalSec: scene.planets[name]?.heartbeat?.interval_s ?? undefined,
      sinceLastSec: last == null ? null : (tHand - last) / 1000,
    }, { audible: name === audible });
    swells[name] = hb?.swell ?? 0;
    keys.push(name);
  }
  return { swells, keys };
}

/** The narration for a record: "rover-1 · camera: saw a face". Self-reports say nothing. */
export function narrationOf(e) {
  if (!e || e.category === "self") return null;
  return `${e.member}: ${e.summary || e.type}`;
}

export default function SystemView({
  scene, frame, edges, cx, cy, size, alpha = 1, ltmsTarget, skipLtms = false, selected, height = 420,
  soundEngine = sharedSound,
}) {
  const canvas = useRef(null);
  const props = useRef(null);
  props.current = { frame, cx, cy, size, alpha, ltmsTarget, skipLtms, selected, height };

  useEffect(() => {
    const el = canvas.current;
    let ctx = null;
    try {
      ctx = el?.getContext?.("2d") ?? null;
    } catch {
      ctx = null;
    }
    if (!ctx) return undefined; // no canvas (tests): the table twin carries the data
    const motion = createSystemMotion(scene, { edges });
    const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (fn) => setTimeout(() => fn(performance.now()), 50);
    const caf = typeof cancelAnimationFrame === "function" ? cancelAnimationFrame : clearTimeout;
    let id = null;
    const beating = new Set();
    const loop = () => {
      const p = props.current;
      const now = performance.now();
      const dpr = window.devicePixelRatio || 1;
      const W = el.clientWidth || 640;
      const H = p.height;
      if (el.width !== Math.round(W * dpr) || el.height !== Math.round(H * dpr)) {
        el.width = Math.round(W * dpr);
        el.height = Math.round(H * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const geo = defaultGeometry(W, H);
      const still = !p.frame.animate;
      const layout = layoutSystem(scene, {
        cx: p.cx ?? geo.cx, cy: p.cy ?? geo.cy, size: p.size ?? geo.size, timeS: now / 1000, still,
      });
      const ltms = p.ltmsTarget ?? geo.ltmsTarget;
      const state = p.frame.clock?.getState?.() ?? p.frame;
      const tHand = state.t;
      const speed = state.mode === "live" ? 1 : state.speed || 1;
      const fresh = motion.advance(tHand, now, {
        animate: !still, layout, ltms, windowMs: 3000 * speed, onLand: (cat) => soundEngine.pulse(cat),
      });
      let swells;
      if (!still) {
        const hb = systemHeartbeats(soundEngine, scene, motion, { tHand, audible: audibleMember(p.selected, scene) });
        swells = hb.swells;
        for (const k of hb.keys) beating.add(k);
      }
      for (let i = fresh.length - 1; i >= 0; i--) {
        const text = narrationOf(fresh[i]);
        if (text) {
          p.frame.narrate?.(text);
          break;
        }
      }
      drawSystem(ctx, scene, layout, {
        nowMs: now, tHand, still, alpha: p.alpha, focus: scene.focus, selected: p.selected, skipLtms: p.skipLtms,
        ltms, flights: motion.flights, ltmsFlash: motion.flasher.level(now), lit: motion.lit(now), beats: motion.beats,
        swells,
      });
      id = raf(loop);
    };
    id = raf(loop);
    return () => {
      caf(id);
      for (const k of beating) soundEngine.forget(k); // planets leave with the scene, or on unmount
    };
  }, [scene, edges, soundEngine]);

  return <canvas ref={canvas} style={{ display: "block", width: "100%", height }} data-testid="system-view" />;
}
