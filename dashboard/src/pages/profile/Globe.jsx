// The globe (#405, docs/holo-light.md "Geography"), on canvas. Scope-free, so
// the fleet-wide Geography page (#382) draws the same globe with every swarm.
//
// - An orthographic globe: land from world-atlas (land-110m below 4×, land-50m
//   above; both bundled, nothing from a CDN), a faint graticule, an ltms-core rim.
// - LTMS is a rotating pyramid over the server's declared place, on a thin
//   column; off screen it is pinned to the frame edge with an arrow.
// - Swarms are breathing lights (size from the log of their records, swelling
//   with their members' heartbeats). A network estimate is a dashed disc of its
//   accuracy radius, never a point. Lights of one swarm at several sites are
//   joined by a faint line.
// - Each record is a light travelling a raised great-circle arc to LTMS, in its
//   category color, taking as long as its measured transit (lib/topology.js travelMs).
// - Cloud agents are satellites on a dashed orbit; unplaced members wait in a
//   ring at the frame corner.
// - Zooming into a swarm (5× → 12×) fades its light out and its star system
//   (SystemView's drawSystem, the same drawing as Topology) in, centred on its
//   place, over the map dimmed to 45%. The pyramid stays on top.
//
// Props:
//   geo         lib/geography.js buildGeography(...)
//   server      {lon, lat, label} or null (the LTMS server's declared place)
//   events      every record in range, sorted: [{t, member, transit, category, source, summary}]
//   sceneFor    (light) => a lib/topology.js scene for the light's swarm (or member), or null
//   heartbeats  {member: interval_s}
//   frame       VizFrame's frame
//   camera      a lib/globe.js createGlobeCamera (owned by the parent, so the touch list drives it)
//   focus       {lightId} the light "Into" chose (optional); otherwise the light nearest the centre
//   audible     the member whose heartbeat is heard (optional)
//   onTouch     (id) => void, for a click on a light (same ids as the touch list)
//   height      CSS px (default 460)
import { useEffect, useRef } from "react";
import { feature } from "topojson-client";
import land110 from "world-atlas/land-110m.json";
import { breathe, drawGlow, pyramid, withAlpha } from "../../lib/light.js";
import { createEventCursor } from "../../lib/replay.js";
import { sound } from "../../lib/sound.js";
import {
  MAP_DIM, globeRadius, graticuleLines, greatCircleAt, landDetail, limbRing, lineRuns, pinToEdge, project,
  projectAlt, ringCaps, ringsOf, systemFade, viewRadiusDeg, visibleCaps, zoomAt,
} from "../../lib/globe.js";
import { discPx, lightOf, lightRadius, markOf, redactAddresses } from "../../lib/geography.js";
import { categoryColor, createArrivalFlasher, layoutSystem, travelMs } from "../../lib/topology.js";
import { LIGHT, LTMS_CORE, NEUTRAL, RELAY, ROLES } from "../../lib/visual.js";
import { HOLO } from "../../lib/theme.js";
import { createSystemMotion, drawSystem, narrationOf } from "./SystemView.jsx";

const LAND = { "110m": ringCaps(ringsOf(feature(land110, land110.objects.land))), "50m": null };
let land50Loading = null;
/** land-50m is bundled as its own chunk and loaded the first time the globe zooms past 4×. */
function ensureLand50() {
  if (LAND["50m"] || land50Loading) return;
  land50Loading = import("world-atlas/land-50m.json").then((m) => {
    const topo = m.default ?? m;
    LAND["50m"] = ringCaps(ringsOf(feature(topo, topo.objects.land)));
  }).catch(() => {
    land50Loading = null; // stay on 110m
  });
}
const GRATICULE = graticuleLines(30);

const MAX_FLIGHTS = 400;
const WINDOW_MS = 3000; // records further behind the hand land without travelling
const STEP_MS = 1000; // Calm view: an arrival is one brightness step for a second
const ARC_LIFT = 0.1;
const SAT_ORBIT = 1.32; // satellites' orbit radius, × R
const DRAG_PX = 5; // a press that moves less is a click

function rgbVar(el, name, fallback) {
  try {
    const v = getComputedStyle(el).getPropertyValue(`--c-${name}`).trim();
    if (v) return `rgb(${v.split(/\s+/).join(", ")})`;
  } catch {
    // no styles (tests): the Holo values
  }
  return fallback;
}

/** Where the LTMS pyramid hovers: over the server's place, lower as the globe zooms in. */
function ltmsAlt(zoom) {
  return 0.14 / Math.max(1, zoom / 3);
}

/** Globe-level state that follows the hand: flights, arrivals, heartbeats. Pure enough to test. */
export function createGlobeMotion(events) {
  const cursor = createEventCursor(events);
  const flights = [];
  const flasher = createArrivalFlasher(3);
  const lastSelf = {};
  let stepUntil = 0;
  let primed = false;
  return {
    flights,
    flasher,
    lastSelf,
    get stepUntil() {
      return stepUntil;
    },
    /** Advance to the hand; returns the fresh records. `from(e)` gives a flight's origin or null. */
    advance(tHand, nowMs, { animate, from, windowMs = WINDOW_MS }) {
      const { fresh, reset } = cursor.advance(tHand);
      if (reset) {
        flights.length = 0;
        flasher.reset();
        for (const k of Object.keys(lastSelf)) delete lastSelf[k];
        for (const e of cursor.upTo(tHand)) if (e.category === "self") lastSelf[e.member] = e.t;
      }
      if (!primed) {
        primed = true;
        for (const e of cursor.upTo(tHand)) if (e.category === "self") lastSelf[e.member] = e.t;
        return [];
      }
      for (const e of fresh) {
        if (e.category === "self") {
          lastSelf[e.member] = e.t;
          continue; // self-reports are the heartbeat, not a stream
        }
        if (tHand - e.t > windowMs) continue;
        if (!animate) {
          stepUntil = nowMs + STEP_MS;
          continue;
        }
        const origin = from(e);
        if (origin && flights.length < MAX_FLIGHTS) {
          flights.push({ origin, member: e.member, color: categoryColor(e.category), born: nowMs, life: travelMs(e.transit) });
        } else {
          flasher.arrive(categoryColor(e.category), nowMs);
        }
      }
      for (let i = flights.length - 1; i >= 0; i--) {
        if (nowMs - flights[i].born >= flights[i].life) {
          flasher.arrive(flights[i].color, nowMs);
          flights.splice(i, 1);
        }
      }
      return fresh;
    },
  };
}

function label(ctx, text, x, y, color, alpha, align = "left", font = '500 12px "DM Sans", sans-serif') {
  ctx.save();
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
  ctx.font = font;
  ctx.textAlign = align;
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.restore();
}

function drawLand(ctx, caps, rot, R, cx, cy, W, H, colors) {
  const list = visibleCaps(caps, rot, viewRadiusDeg(W, H, R));
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.clip();
  ctx.beginPath();
  for (const { ring } of list) {
    const pts = limbRing(ring, rot, R, cx, cy);
    if (!pts) continue;
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
  }
  ctx.fillStyle = colors.land;
  ctx.fill("evenodd");
  ctx.strokeStyle = colors.edge;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();
}

/** The light nearest the frame centre that is on the near side, or null. */
function nearestLight(geo, rot, R, cx, cy) {
  let best = null;
  for (const l of geo.lights) {
    const p = project(l.lon, l.lat, rot, R, cx, cy);
    if (!p.visible) continue;
    const d = Math.hypot(p.x - cx, p.y - cy);
    if (!best || d < best.d) best = { light: l, d };
  }
  return best?.light ?? null;
}

export default function Globe({
  geo, server, events, sceneFor, heartbeats = {}, frame, camera, focus, audible, onTouch, height = 460,
}) {
  const canvas = useRef(null);
  const props = useRef(null);
  const hits = useRef([]);
  props.current = { geo, server, sceneFor, heartbeats, frame, camera, focus, audible, onTouch, height };

  useEffect(() => {
    const el = canvas.current;
    let ctx = null;
    try {
      ctx = el?.getContext?.("2d") ?? null;
    } catch {
      ctx = null;
    }
    if (!ctx) return undefined; // no canvas (tests): the touch list and table twin carry the data
    const motion = createGlobeMotion(events);
    let system = null; // {key, scene, motion}
    let colors = null;
    let colorsAt = -Infinity;
    const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (fn) => setTimeout(() => fn(performance.now()), 50);
    const caf = typeof cancelAnimationFrame === "function" ? cancelAnimationFrame : clearTimeout;
    let id = null;
    const beating = new Set();

    const loop = () => {
      const p = props.current;
      const server = p.server;
      const now = performance.now();
      if (now - colorsAt > 2000) {
        colorsAt = now;
        colors = {
          land: rgbVar(el, "ink-raised", HOLO["ink-raised"]), edge: rgbVar(el, "ink-line", HOLO["ink-line"]),
          paper: "#E7EAF0", faint: HOLO["paper-faint"],
        };
      }
      const dpr = window.devicePixelRatio || 1;
      const W = el.clientWidth || 640;
      const H = p.height;
      if (el.width !== Math.round(W * dpr) || el.height !== Math.round(H * dpr)) {
        el.width = Math.round(W * dpr);
        el.height = Math.round(H * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const still = !p.frame.animate;
      const view = p.camera.step(now, { animate: !still });
      const R = globeRadius(W, H, view.zoom);
      const cx = W / 2;
      const cy = H / 2;
      const rot = view;
      const z = systemFade(view.zoom);
      const ga = 1 - z;
      const g = p.geo;
      const frameHits = [];
      if (landDetail(view.zoom) === "50m") ensureLand50();
      const caps = (landDetail(view.zoom) === "50m" && LAND["50m"]) || LAND["110m"];

      // The globe: rim, disc, graticule, land.
      const rim = ctx.createRadialGradient?.(cx, cy, R * 0.92, cx, cy, R * 1.15);
      if (rim) {
        rim.addColorStop(0, withAlpha(LTMS_CORE, 0.19));
        rim.addColorStop(1, withAlpha(LTMS_CORE, 0));
        ctx.fillStyle = rim;
        ctx.beginPath();
        ctx.arc(cx, cy, R * 1.15, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.fillStyle = "#03070d";
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();
      ctx.save();
      ctx.strokeStyle = colors.edge;
      ctx.globalAlpha = 0.6;
      ctx.lineWidth = 0.6;
      ctx.beginPath();
      for (const line of GRATICULE) {
        for (const run of lineRuns(line, rot, R, cx, cy)) {
          ctx.moveTo(run[0].x, run[0].y);
          for (let i = 1; i < run.length; i++) ctx.lineTo(run[i].x, run[i].y);
        }
      }
      ctx.stroke();
      ctx.restore();
      drawLand(ctx, caps, rot, R, cx, cy, W, H, colors);
      ctx.save();
      ctx.strokeStyle = LTMS_CORE;
      ctx.globalAlpha = 0.45;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      // Where things are on screen.
      const lightAt = Object.fromEntries(g.lights.map((l) => [l.id, project(l.lon, l.lat, rot, R, cx, cy)]));
      const satAt = {};
      const orbitR = R * SAT_ORBIT;
      g.satellites.forEach((s, i) => {
        const a = -Math.PI * 0.8 + (2 * Math.PI * i) / Math.max(1, g.satellites.length);
        satAt[s.id] = { x: cx + Math.cos(a) * orbitR, y: cy + Math.sin(a) * orbitR * 0.42 };
      });
      let ltmsPin;
      let column = null;
      if (server) {
        const surface = project(server.lon, server.lat, rot, R, cx, cy);
        const raised = projectAlt(server.lon, server.lat, ltmsAlt(view.zoom), rot, R, cx, cy);
        ltmsPin = pinToEdge(raised, W, H, 46, !raised.visible);
        if (!ltmsPin.edge && surface.visible) column = [surface, raised];
      } else {
        ltmsPin = { x: W / 2, y: 46, edge: false, arrow: null };
      }
      const L = { x: ltmsPin.x, y: ltmsPin.y };

      // The hand: flights, heartbeats, narration, sound.
      const state = p.frame.clock?.getState?.() ?? p.frame;
      const tHand = state.t;
      const speed = state.mode === "live" ? 1 : state.speed || 1;
      const memberLight = (name) => lightOf(g, name);
      const fresh = motion.advance(tHand, now, {
        animate: !still,
        windowMs: WINDOW_MS * speed,
        from: (e) => {
          const l = memberLight(e.member);
          if (l) return { kind: "light", id: l.id };
          const s = g.satellites.find((x) => x.name === e.member);
          return s ? { kind: "sat", id: s.id } : null;
        },
      });
      let said = false;
      for (let i = fresh.length - 1; i >= 0; i--) {
        const e = fresh[i];
        if (e.category === "self") continue;
        if (tHand - e.t <= WINDOW_MS * speed) sound.pulse(e.category);
        if (!said) {
          const text = narrationOf(e);
          if (text) {
            p.frame.narrate?.(redactAddresses(text));
            said = true;
          }
        }
      }
      // Heartbeats: a light swells with the sum of its members' beats.
      const swellOf = {};
      if (!still) {
        for (const l of g.lights) {
          let sw = 0;
          for (const name of l.members) {
            const last = motion.lastSelf[name];
            const hb = sound.heartbeat(name, {
              intervalSec: p.heartbeats[name] ?? undefined,
              sinceLastSec: last == null ? null : (tHand - last) / 1000,
            }, { audible: name === p.audible });
            beating.add(name);
            sw += hb.swell;
          }
          swellOf[l.id] = Math.min(1, sw);
        }
      }

      // Globe level (fades as a system fades in).
      if (ga > 0.02) {
        ctx.save();
        ctx.globalAlpha = 0.35 * ga;
        ctx.strokeStyle = RELAY;
        ctx.lineWidth = 1;
        for (const [a, b] of g.links) {
          const la = g.lights.find((l) => l.id === a);
          const lb = g.lights.find((l) => l.id === b);
          if (!la || !lb) continue;
          ctx.beginPath();
          let started = false;
          for (let i = 0; i <= 24; i++) {
            const q = greatCircleAt(la, lb, i / 24, 0.02);
            const s = projectAlt(q.lon, q.lat, q.alt, rot, R, cx, cy);
            if (!s.visible) { started = false; continue; }
            if (started) ctx.lineTo(s.x, s.y); else ctx.moveTo(s.x, s.y);
            started = true;
          }
          ctx.stroke();
        }
        // Stream paths: dashed relay, "data could move here".
        if (server) {
          ctx.setLineDash([3, 6]);
          ctx.globalAlpha = 0.3 * ga;
          for (const l of g.lights) {
            ctx.beginPath();
            let started = false;
            for (let i = 0; i <= 30; i++) {
              const q = greatCircleAt(l, server, i / 30, ARC_LIFT);
              const s = projectAlt(q.lon, q.lat, q.alt, rot, R, cx, cy);
              if (!s.visible) { started = false; continue; }
              if (started) ctx.lineTo(s.x, s.y); else ctx.moveTo(s.x, s.y);
              started = true;
            }
            ctx.stroke();
          }
          ctx.setLineDash([]);
        }
        ctx.restore();

        // Satellites on their dashed orbit.
        if (g.satellites.length) {
          ctx.save();
          ctx.strokeStyle = RELAY;
          ctx.globalAlpha = 0.45 * ga;
          ctx.setLineDash([2, 7]);
          ctx.beginPath();
          ctx.ellipse(cx, cy, orbitR, orbitR * 0.42, 0, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
          for (const s of g.satellites) {
            const at = satAt[s.id];
            drawGlow(ctx, at.x, at.y, LTMS_CORE, 10, LIGHT.lit * ga);
            drawGlow(ctx, at.x, at.y, "#ffffff", 3, ga);
            label(ctx, s.name, at.x + 12, at.y + 4, RELAY, ga, "left", '500 11px "DM Sans", sans-serif');
            frameHits.push({ id: s.id, x: at.x, y: at.y, r: 22 });
          }
        }

        // Swarm lights: estimates as dashed discs, declared places as points.
        for (const l of g.lights) {
          const at = lightAt[l.id];
          if (!at.visible) continue;
          const mark = markOf(l);
          const color = l.swarm ? ROLES.hub.color : ROLES.free.color;
          const sw = swellOf[l.id] ?? 0;
          const base = still ? LIGHT.breathing.rest : breathe(now);
          const r = lightRadius(l.records) * (1 + 0.15 * sw);
          if (mark.shape === "disc") {
            const dr = discPx(mark.radiusKm, R);
            ctx.save();
            ctx.globalAlpha = 0.12 * ga;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(at.x, at.y, dr, 0, Math.PI * 2);
            ctx.fill();
            ctx.globalAlpha = 0.6 * ga;
            ctx.strokeStyle = color;
            ctx.setLineDash([3, 5]);
            ctx.stroke();
            ctx.restore();
            drawGlow(ctx, at.x, at.y, color, Math.max(r, dr * 0.6), (base + (LIGHT.live - LIGHT.lit) * sw) * 0.6 * ga);
          } else {
            drawGlow(ctx, at.x, at.y, color, r * 1.6, (base + (LIGHT.live - LIGHT.lit) * sw) * ga);
            drawGlow(ctx, at.x, at.y, "#ffffff", 4, 0.9 * ga);
          }
          if (ga > 0.3) label(ctx, l.place ? `${l.label} · ${l.place}` : l.label, at.x + 16, at.y + 18, colors.paper, ga);
          frameHits.push({ id: l.id, x: at.x, y: at.y, r: Math.max(16, r) });
        }

        // Records travelling to LTMS.
        for (const f of motion.flights) {
          const u = Math.min(1, (now - f.born) / f.life);
          let pos = null;
          if (f.origin.kind === "light" && server) {
            const l = g.lights.find((x) => x.id === f.origin.id);
            if (l) {
              const q = greatCircleAt(l, server, u, ARC_LIFT);
              const s = projectAlt(q.lon, q.lat, q.alt, rot, R, cx, cy);
              if (s.visible) pos = s;
            }
          } else {
            const a = f.origin.kind === "sat" ? satAt[f.origin.id] : lightAt[f.origin.id];
            if (a) {
              const lift = Math.sin(Math.PI * u) * 40;
              pos = { x: a.x + (L.x - a.x) * u, y: a.y + (L.y - a.y) * u - lift };
            }
          }
          if (pos) drawGlow(ctx, pos.x, pos.y, f.color, 8, ga);
        }
      }

      // Unplaced: a ring at the frame corner, each with its reason (in the touch list).
      if (g.unplaced.length) {
        const ucx = 56;
        const ucy = H - 56;
        ctx.save();
        ctx.strokeStyle = RELAY;
        ctx.globalAlpha = 0.5;
        ctx.setLineDash([2, 6]);
        ctx.beginPath();
        ctx.arc(ucx, ucy, 30, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
        g.unplaced.forEach((u, i) => {
          const a = -Math.PI / 2 + (2 * Math.PI * i) / g.unplaced.length;
          const x = ucx + Math.cos(a) * 30;
          const y = ucy + Math.sin(a) * 30;
          drawGlow(ctx, x, y, NEUTRAL, 6, LIGHT.asleep * 2);
          frameHits.push({ id: u.id, x, y, r: 16 });
        });
        label(ctx, `Unplaced (${g.unplaced.length})`, ucx, ucy - 40, colors.faint, 1, "center", '500 11px "DM Sans", sans-serif');
      }

      // The LTMS pyramid (and its column) on the map or on the edge.
      const flash = motion.flasher.level(now);
      const stepped = now < motion.stepUntil ? 1 : 0;
      const drawPyramid = () => {
        if (flash) drawGlow(ctx, L.x, L.y, flash.color, 30, Math.min(0.7, flash.a));
        pyramid(ctx, L.x, L.y, ltmsPin.edge ? 14 : 20, still ? 0 : now / 1000, flash ? flash.a : stepped);
      };
      if (column) {
        ctx.save();
        ctx.strokeStyle = LTMS_CORE;
        ctx.globalAlpha = 0.4;
        ctx.beginPath();
        ctx.moveTo(column[0].x, column[0].y);
        ctx.lineTo(column[1].x, column[1].y);
        ctx.stroke();
        ctx.restore();
      }
      drawPyramid();
      frameHits.push({ id: "ltms", x: L.x, y: L.y, r: 30 });

      // Inside a swarm: its star system, centred on its place, over the dimmed map.
      const target = p.focus?.lightId ? g.lights.find((l) => l.id === p.focus.lightId) : null;
      const focusLight = z > 0.02 ? (target ?? nearestLight(g, rot, R, cx, cy)) : null;
      const scene = focusLight ? p.sceneFor?.(focusLight) : null;
      if (scene) {
        const key = focusLight.id;
        if (!system || system.key !== key || system.scene !== scene) {
          system = { key, scene, motion: createSystemMotion(scene) };
        }
        const at = lightAt[focusLight.id];
        ctx.save();
        ctx.fillStyle = `rgba(0, 0, 0, ${MAP_DIM * z})`;
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
        const size = Math.min(W, H) * (0.55 + 0.55 * z);
        const layout = layoutSystem(scene, { cx: at.x, cy: at.y, size, timeS: now / 1000, still });
        system.motion.advance(tHand, now, { animate: !still, layout, ltms: L, windowMs: WINDOW_MS * speed });
        drawSystem(ctx, scene, layout, {
          nowMs: now, tHand, still, alpha: z, focus: scene.focus, skipLtms: true, ltms: L,
          flights: system.motion.flights, lit: system.motion.lit(now), beats: system.motion.beats,
        });
        drawPyramid();
        if (z > 0.5) {
          for (const [name, s] of Object.entries(layout.stars)) frameHits.push({ id: `star:${name}`, x: s.x, y: s.y, r: Math.max(16, s.r * 0.6) });
          for (const [name, pl] of Object.entries(layout.planets)) frameHits.push({ id: `planet:${name}`, x: pl.x, y: pl.y, r: Math.max(16, pl.r) });
        }
      } else if (system && z <= 0.02) {
        system = null;
      }

      label(ctx, ltmsPin.edge ? `LTMS ${ltmsPin.arrow}` : server ? "LTMS" : "LTMS (place not declared)",
            L.x + (ltmsPin.edge && L.x > W / 2 ? -16 : 18), L.y - (ltmsPin.edge ? 16 : 8), LTMS_CORE, 1,
            ltmsPin.edge && L.x > W / 2 ? "right" : "left");
      label(ctx, `zoom ${view.zoom.toFixed(1)}×`, W - 12, H - 12, colors.faint, 1, "right", '400 11px "DM Sans", sans-serif');
      hits.current = frameHits;
      id = raf(loop);
    };
    id = raf(loop);
    return () => {
      caf(id);
      for (const name of beating) sound.forget(name);
    };
  }, [events]);

  // Pointer: drag to turn, wheel or pinch to zoom, click a light to touch it.
  useEffect(() => {
    const el = canvas.current;
    if (!el?.addEventListener) return undefined;
    const pts = new Map();
    let drag = null;
    let pinch = null;
    const pos = (e) => {
      const r = el.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const radius = () => globeRadius(el.clientWidth || 640, props.current.height, props.current.camera.view.zoom);
    const down = (e) => {
      pts.set(e.pointerId, pos(e));
      el.setPointerCapture?.(e.pointerId);
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, zoom: props.current.camera.view.zoom };
        drag = null;
      } else if (pts.size === 1) {
        drag = { ...pos(e), moved: 0 };
      }
    };
    const move = (e) => {
      if (!pts.has(e.pointerId)) return;
      const p = pos(e);
      const prev = pts.get(e.pointerId);
      pts.set(e.pointerId, p);
      const cam = props.current.camera;
      if (pinch && pts.size === 2) {
        const [a, b] = [...pts.values()];
        const f = (pinch.zoom * (Math.hypot(a.x - b.x, a.y - b.y) / pinch.d)) / cam.view.zoom;
        cam.zoomBy(f, performance.now());
      } else if (drag) {
        drag.moved = Math.max(drag.moved, Math.hypot(p.x - drag.x, p.y - drag.y));
        if (drag.moved >= DRAG_PX) cam.drag(p.x - prev.x, p.y - prev.y, radius(), performance.now());
      }
    };
    const up = (e) => {
      const was = drag;
      pts.delete(e.pointerId);
      if (pts.size < 2) pinch = null;
      if (pts.size === 0) drag = null;
      if (was && was.moved < DRAG_PX && e.type === "pointerup") {
        const p = pos(e);
        let best = null;
        for (const h of hits.current) {
          const d = Math.hypot(h.x - p.x, h.y - p.y);
          if (d <= h.r && (!best || d < best.d)) best = { id: h.id, d };
        }
        if (best) props.current.onTouch?.(best.id);
      }
    };
    const wheel = (e) => {
      e.preventDefault();
      props.current.camera.zoomBy(Math.exp(-e.deltaY * 0.0015), performance.now());
    };
    const dbl = (e) => {
      const p = pos(e);
      const W = el.clientWidth || 640;
      const H = props.current.height;
      const cam = props.current.camera;
      const out = zoomAt(p.x, p.y, cam.view, globeRadius(W, H, cam.view.zoom), W / 2, H / 2);
      if (out) cam.moveTo(out.view, performance.now(), { instant: !props.current.frame.animate });
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    el.addEventListener("wheel", wheel, { passive: false });
    el.addEventListener("dblclick", dbl);
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      el.removeEventListener("wheel", wheel);
      el.removeEventListener("dblclick", dbl);
    };
  }, []);

  return (
    <canvas ref={canvas} data-testid="globe"
            style={{ display: "block", width: "100%", height, touchAction: "none", cursor: "grab" }} />
  );
}
