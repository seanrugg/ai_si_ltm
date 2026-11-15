// Holo Light canvas helpers (#400, docs/holo-light.md), shared by every view
// (Geography, Topology, Chronology). Every light is data: these draw lights,
// the views decide which record or state each one stands for.
//
// The maths here is pure and unit-tested. The drawing functions only touch the
// context they are given.
import { LIGHT, LTMS_CORE } from "./visual.js";

// ---------------------------------------------------------------- maths

/** Hermite smoothstep: 0 at or below e0, 1 at or above e1, smooth between. */
export function smoothstep(e0, e1, x) {
  if (e0 === e1) return x < e0 ? 0 : 1;
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/** Cubic ease-out, for reveals (600 ms between levels). */
export function easeOutCubic(t) {
  const u = 1 - Math.min(1, Math.max(0, t));
  return 1 - u * u * u;
}

/**
 * Breathing opacity at `nowMs`: present and waiting. A slow ease in-out cycle
 * between LIGHT.breathing.min and max over its period (3.2 s).
 * Pass `still` (Calm view, reduced motion) to get the steady resting value.
 */
export function breathe(nowMs, still = false) {
  const { min, max, rest, periodMs } = LIGHT.breathing;
  if (still) return rest;
  const phase = (((nowMs % periodMs) + periodMs) % periodMs) / periodMs;
  const wave = (1 - Math.cos(phase * 2 * Math.PI)) / 2; // 0..1..0, eased at both ends
  return min + (max - min) * wave;
}

// Heartbeat timings (docs/holo-light.md, "Heartbeat" and "Sound").
export const HEARTBEAT = {
  dubDelayMs: 120, // lub, then dub 120 ms later
  attackMs: 40, // no clicks: 30–60 ms
  decayMs: 180,
  dubLevel: 0.8, // the second swell is a little softer
  minBpm: 48,
  maxBpm: 60,
};

function swell(dt) {
  const { attackMs, decayMs } = HEARTBEAT;
  if (dt < 0 || dt > attackMs + decayMs) return 0;
  if (dt <= attackMs) return smoothstep(0, attackMs, dt);
  return 1 - smoothstep(attackMs, attackMs + decayMs, dt);
}

/**
 * The lub-dub envelope, 0..1, at `now` (ms) for a beat that started at
 * `lastBeat` (ms). Two swells 120 ms apart, then rest. 0 before the beat, after
 * it, or when there is no beat (`lastBeat` null).
 * A member's light scales 1.0 → 1.08 and brightens lit → live with it.
 */
export function heartbeatSwell(now, lastBeat) {
  if (lastBeat == null || !Number.isFinite(now) || !Number.isFinite(lastBeat)) return 0;
  const dt = now - lastBeat;
  const lub = swell(dt);
  const dub = HEARTBEAT.dubLevel * swell(dt - HEARTBEAT.dubDelayMs);
  return Math.min(1, Math.max(lub, dub));
}

/**
 * Heart rate (BPM) from the usual interval between self-reports, in seconds.
 * Resting range only: every 30 s or slower beats at 48; faster reporting raises
 * it toward 60 and never higher, so a heartbeat never sounds anxious.
 */
export function heartbeatBpm(intervalSec) {
  const { minBpm, maxBpm } = HEARTBEAT;
  if (!Number.isFinite(intervalSec) || intervalSec <= 0) return minBpm;
  // 30 s → 48, 1 s → 60, linear in between.
  const t = 1 - smoothstep(1, 30, intervalSec);
  return minBpm + (maxBpm - minBpm) * t;
}

/**
 * Flash limiter: at most 3 flashes a second for any one light (WCAG 2.3.1).
 * `gate.allow(id, nowMs)` is true when that light may flash now and records it;
 * otherwise the caller merges the arrival into the light's next flash.
 */
export function createFlashGate(maxPerSecond = 3) {
  const minGap = 1000 / maxPerSecond;
  const last = new Map();
  return {
    allow(id, nowMs) {
      const prev = last.get(id);
      if (prev != null && nowMs - prev < minGap) return false;
      last.set(id, nowMs);
      return true;
    },
    reset() {
      last.clear();
    },
  };
}

// ---------------------------------------------------------------- glow sprites

const GLOW_CACHE_MAX = 256;
const glowCache = new Map();

function defaultCanvas(size) {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(size, size);
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  return c;
}

/**
 * A cached radial-gradient glow sprite: a square canvas 2·radius wide, `color`
 * at full strength in the middle fading to transparent at the edge. Sprites are
 * cached by color and whole-pixel radius (at most 256, oldest dropped first),
 * so a view can draw thousands of lights a frame without new gradients.
 * `makeCanvas(size)` is for tests and workers; it defaults to OffscreenCanvas or
 * a <canvas>. Returns null when no 2D context is available (e.g. jsdom).
 */
export function glowSprite(color, radius, makeCanvas = defaultCanvas) {
  const r = Math.max(1, Math.round(radius));
  const key = `${color}|${r}`;
  if (glowCache.has(key)) {
    const hit = glowCache.get(key);
    glowCache.delete(key); // refresh its place: least recently used goes first
    glowCache.set(key, hit);
    return hit;
  }
  const size = r * 2;
  const canvas = makeCanvas(size);
  const g = canvas?.getContext?.("2d");
  if (!g) return null;
  const grad = g.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0, color);
  grad.addColorStop(0.25, withAlpha(color, 0.55));
  grad.addColorStop(1, withAlpha(color, 0));
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  glowCache.set(key, canvas);
  if (glowCache.size > GLOW_CACHE_MAX) glowCache.delete(glowCache.keys().next().value);
  return canvas;
}

export function glowCacheSize() {
  return glowCache.size;
}

export function clearGlowCache() {
  glowCache.clear();
}

/** "#rgb" / "#rrggbb" → "rgba(r, g, b, a)". Other color strings pass through. */
export function withAlpha(color, alpha) {
  let hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color)?.[1];
  if (!hex) return color;
  if (hex.length === 3) hex = hex.split("").map((ch) => ch + ch).join("");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Draw one light: the cached glow sprite centred on (x, y), added with
 * globalCompositeOperation "lighter" so overlapping lights brighten. `alpha` is
 * a rung of the brightness ladder (LIGHT.*).
 */
export function drawGlow(ctx, x, y, color, radius, alpha = LIGHT.lit) {
  const sprite = glowSprite(color, radius);
  if (!sprite) return;
  const r = sprite.width / 2;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.globalAlpha = Math.min(1, Math.max(0, alpha));
  ctx.drawImage(sprite, x - r, y - r);
  ctx.restore();
}

// ---------------------------------------------------------------- the LTMS pyramid

// Rotation: slow, because nothing spins for show (radians per second).
export const PYRAMID_SPIN = 0.35;
const TILT = 0.45; // radians: how far the viewer looks down on the pyramid

/** The pyramid's edges, as pairs of vertex indexes (4 base corners, then the apex). */
export const PYRAMID_EDGES = [[0, 1], [1, 2], [2, 3], [3, 0], [0, 4], [1, 4], [2, 4], [3, 4]];

/**
 * The square-based LTMS pyramid, turned `angle` radians about its vertical
 * axis, projected to 2D around (0, 0): 4 base corners then the apex, each
 * {x, y, z} with y down (canvas) and z toward the viewer. `size` is the base
 * width; the pyramid is as tall as it is wide.
 */
export function pyramidPoints(size, angle) {
  const h = size / 2;
  const base = [[-h, -h], [h, -h], [h, h], [-h, h]].map(([bx, bz]) => [bx, h / 2, bz]);
  const apex = [0, -h * 1.5, 0];
  const cosA = Math.cos(angle), sinA = Math.sin(angle);
  const cosT = Math.cos(TILT), sinT = Math.sin(TILT);
  return [...base, apex].map(([px, py, pz]) => {
    const x = px * cosA - pz * sinA;
    const z0 = px * sinA + pz * cosA;
    const y = py * cosT - z0 * sinT;
    const z = py * sinT + z0 * cosT;
    return { x, y, z };
  });
}

/**
 * Draw the rotating wireframe LTMS pyramid centred at (x, y). `t` is seconds
 * (pass a constant in Calm view so it stands still); `flash` 0..1 brightens it
 * when a record arrives. Drawn in `ltms-core` light with "lighter" compositing.
 */
export function pyramid(ctx, x, y, size, t, flash = 0) {
  const f = Math.min(1, Math.max(0, flash || 0));
  const pts = pyramidPoints(size, (t || 0) * PYRAMID_SPIN);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = LTMS_CORE;
  ctx.lineWidth = Math.max(1, size / 40);
  ctx.lineJoin = "round";
  for (const [a, b] of PYRAMID_EDGES) {
    // Edges at the back are dimmer, so the wireframe reads as solid.
    const back = (pts[a].z + pts[b].z) / 2 < 0;
    ctx.globalAlpha = (back ? LIGHT.trail : LIGHT.lit) + (LIGHT.live - LIGHT.lit) * f;
    ctx.beginPath();
    ctx.moveTo(x + pts[a].x, y + pts[a].y);
    ctx.lineTo(x + pts[b].x, y + pts[b].y);
    ctx.stroke();
  }
  ctx.restore();
  const apex = pts[4];
  drawGlow(ctx, x + apex.x, y + apex.y, LTMS_CORE, size * (0.25 + 0.25 * f), LIGHT.lit + (LIGHT.live - LIGHT.lit) * f);
}

// ---------------------------------------------------------------- Calm view

/** True when the system asks for reduced motion. */
export function prefersReducedMotion() {
  try {
    return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  } catch {
    return false;
  }
}

const CALM_KEY = "ltms.calm";

/** The per-user setting "Start visualizations in Calm view" (this browser). */
export function getCalmStart() {
  try {
    return window.localStorage.getItem(CALM_KEY) === "1";
  } catch {
    return false;
  }
}

export function setCalmStart(on) {
  try {
    if (on) window.localStorage.setItem(CALM_KEY, "1");
    else window.localStorage.removeItem(CALM_KEY);
  } catch {
    // storage unavailable: the choice applies for this visit only
  }
  return Boolean(on);
}
