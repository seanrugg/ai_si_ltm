// Orthographic globe maths (issues #195, #405). Pure functions, no map library.
// A view is { lon, lat, zoom }: the point at the centre of the disc, and zoom.
// Geography (#405) draws the globe on canvas with these; the fleet-wide
// Geography page (#382) reuses the same camera and projection.
import { smoothstep } from "./light.js";

const RAD = Math.PI / 180;
export const EARTH_KM = 6371;

// Zoom (#405): 0.9× (a little smaller than the frame) to 30× (a city).
export const MIN_ZOOM = 0.9;
export const MAX_ZOOM = 30;
/** Above this zoom the land is drawn from world-atlas land-50m, below from land-110m. */
export const DETAIL_ZOOM = 4;
/** Zooming into a swarm: its star system fades in between these zooms. */
export const SYSTEM_FADE = [5, 12];
/** Zoom used by "Into <swarm>": well inside the fade, so the system is whole. */
export const INTO_ZOOM = 14;
/** Easing animations (zoom, "Into", "Whole globe"); instant with reduced motion. */
export const EASE_MS = 900;
/** The globe drifts this slowly, and only after IDLE_MS without input. */
export const DRIFT_DEG_PER_S = 0.3;
export const IDLE_MS = 20_000;
/** The globe's radius at zoom 1, as a fraction of min(width, height). */
export const BASE_RADIUS = 0.36;
/** Dim over the map while a star system is shown (multiplied by the fade). */
export const MAP_DIM = 0.45;

export function clampZoom(z) {
  if (!Number.isFinite(z)) return MIN_ZOOM;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

/** The globe's radius in px for a W × H stage at `zoom`. */
export function globeRadius(W, H, zoom) {
  return Math.min(W, H) * BASE_RADIUS * zoom;
}

/** The star system's fade at `zoom`: z = smoothstep(5, 12, zoom). 0 = globe, 1 = inside the swarm. */
export function systemFade(zoom) {
  return smoothstep(SYSTEM_FADE[0], SYSTEM_FADE[1], zoom);
}

/** Which world-atlas land to draw at `zoom`: "50m" above 4×, else "110m". */
export function landDetail(zoom) {
  return zoom > DETAIL_ZOOM ? "50m" : "110m";
}

/** Project lon/lat (degrees) with the view centred on rot = {lon, lat}. */
export function project(lon, lat, rot, R, cx, cy) {
  const l = (lon - rot.lon) * RAD;
  const p = lat * RAD;
  const p0 = rot.lat * RAD;
  const x = R * Math.cos(p) * Math.sin(l);
  const y = R * (Math.cos(p0) * Math.sin(p) - Math.sin(p0) * Math.cos(p) * Math.cos(l));
  const visible = Math.sin(p0) * Math.sin(p) + Math.cos(p0) * Math.cos(p) * Math.cos(l) >= 0;
  return { x: cx + x, y: cy - y, visible };
}

/**
 * Project a point `alt` (a fraction of R) above the surface. A raised point is
 * visible when it is on the near side or sticks out past the limb.
 */
export function projectAlt(lon, lat, alt, rot, R, cx, cy) {
  const s = project(lon, lat, rot, R, cx, cy);
  const k = 1 + (alt || 0);
  const x = cx + (s.x - cx) * k;
  const y = cy + (s.y - cy) * k;
  return { x, y, visible: s.visible || Math.hypot(x - cx, y - cy) > R };
}

/** Screen point back to lon/lat, or null off the disc. */
export function invert(sx, sy, rot, R, cx, cy) {
  const x = sx - cx;
  const y = cy - sy;
  const rho = Math.hypot(x, y);
  if (rho > R) return null;
  if (rho === 0) return { lon: rot.lon, lat: rot.lat };
  const c = Math.asin(rho / R);
  const p0 = rot.lat * RAD;
  const lat = Math.asin(Math.cos(c) * Math.sin(p0) + (y * Math.sin(c) * Math.cos(p0)) / rho) / RAD;
  const lon = rot.lon + Math.atan2(x * Math.sin(c), rho * Math.cos(c) * Math.cos(p0) - y * Math.sin(c) * Math.sin(p0)) / RAD;
  return { lon: ((lon + 540) % 360) - 180, lat };
}

function runs(points) {
  const out = [];
  let cur = [];
  for (const p of points) {
    if (p.visible) cur.push(p);
    else if (cur.length) { out.push(cur); cur = []; }
  }
  if (cur.length) out.push(cur);
  return out;
}

/** Visible runs of a polyline of [lon, lat], each a list of {x, y}. */
export function lineRuns(line, rot, R, cx, cy) {
  return runs(line.map(([lon, lat]) => project(lon, lat, rot, R, cx, cy))).filter((r) => r.length > 1);
}

/** Meridians and parallels every `step` degrees, as polylines of [lon, lat]. */
export function graticuleLines(step = 30) {
  const lines = [];
  for (let lon = -180; lon < 180; lon += step) {
    lines.push(Array.from({ length: 37 }, (_, i) => [lon, -90 + i * 5]));
  }
  for (let lat = -60; lat <= 60; lat += step) {
    lines.push(Array.from({ length: 73 }, (_, i) => [-180 + i * 5, lat]));
  }
  return lines;
}

/**
 * A land ring, ready to fill on a canvas: every point projected, and points on
 * the far side pushed out to the limb, so a ring crossing the horizon closes
 * along the edge of the disc. Returns [{x, y}] or null when no point is visible.
 */
export function limbRing(ring, rot, R, cx, cy) {
  let any = false;
  const out = ring.map(([lon, lat]) => {
    const p = project(lon, lat, rot, R, cx, cy);
    if (p.visible) {
      any = true;
      return { x: p.x, y: p.y };
    }
    const dx = p.x - cx;
    const dy = p.y - cy;
    const d = Math.hypot(dx, dy) || 1;
    return { x: cx + (dx / d) * R, y: cy + (dy / d) * R };
  });
  return any ? out : null;
}

function toVec(lon, lat) {
  const l = lon * RAD;
  const p = lat * RAD;
  return [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)];
}

/**
 * A bounding cap for each ring (its mean direction and angular radius), so
 * rings that can't be on screen are skipped without projecting them.
 * Returns [{ring, c: [x, y, z], radius (degrees)}].
 */
export function ringCaps(rings) {
  return rings.map((ring) => {
    const s = [0, 0, 0];
    const vs = ring.map(([lon, lat]) => toVec(lon, lat));
    for (const v of vs) for (let k = 0; k < 3; k++) s[k] += v[k];
    const n = Math.hypot(...s) || 1;
    const c = s.map((x) => x / n);
    let radius = 0;
    for (const v of vs) {
      const dot = Math.min(1, Math.max(-1, v[0] * c[0] + v[1] * c[1] + v[2] * c[2]));
      radius = Math.max(radius, Math.acos(dot) / RAD);
    }
    if (n < 1e-6) radius = 180; // a ring around a pole: never cull
    return { ring, c, radius };
  });
}

/**
 * The angular radius (degrees) of the globe that can be on a W × H stage at
 * radius R: 90° when the whole disc fits, less when zoomed in.
 */
export function viewRadiusDeg(W, H, R) {
  const half = Math.hypot(W, H) / 2;
  return half >= R ? 90 : Math.asin(half / R) / RAD;
}

/** Caps that may be on screen around view centre `rot`, given a view radius in degrees. */
export function visibleCaps(caps, rot, viewDeg) {
  const v = toVec(rot.lon, rot.lat);
  return caps.filter(({ c, radius }) => {
    const dot = Math.min(1, Math.max(-1, v[0] * c[0] + v[1] * c[1] + v[2] * c[2]));
    return Math.acos(dot) / RAD - radius <= viewDeg;
  });
}

/**
 * A great-circle arc from a to b ({lon, lat}), raised above the surface:
 * points { lon, lat, alt } with alt = lift * sin(pi * t) (a fraction of R).
 */
export function arc(a, b, lift = 0.15, steps = 32) {
  return Array.from({ length: steps + 1 }, (_, i) => greatCircleAt(a, b, i / steps, lift));
}

/** The point `t` (0..1) of the way along the great circle a → b, raised by lift * sin(pi t). */
export function greatCircleAt(a, b, t, lift = 0) {
  const va = toVec(a.lon, a.lat);
  const vb = toVec(b.lon, b.lat);
  const dot = Math.min(1, Math.max(-1, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2]));
  const omega = Math.acos(dot);
  let v;
  if (omega < 1e-6) v = va;
  else {
    const s1 = Math.sin((1 - t) * omega) / Math.sin(omega);
    const s2 = Math.sin(t * omega) / Math.sin(omega);
    v = [0, 1, 2].map((k) => s1 * va[k] + s2 * vb[k]);
  }
  return {
    lon: Math.atan2(v[1], v[0]) / RAD,
    lat: Math.asin(Math.max(-1, Math.min(1, v[2]))) / RAD,
    alt: lift * Math.sin(Math.PI * t),
  };
}

/** Degrees between two lon/lat points along the surface. */
export function distanceDeg(a, b) {
  const va = toVec(a.lon, a.lat);
  const vb = toVec(b.lon, b.lat);
  return Math.acos(Math.min(1, Math.max(-1, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2]))) / RAD;
}

/**
 * Double-click: centre on the clicked point and double the zoom (clamped).
 * Returns { view } or null when the click is off the disc.
 */
export function zoomAt(sx, sy, view, R, cx, cy) {
  const at = invert(sx, sy, view, R, cx, cy);
  if (!at) return null;
  return { view: { lon: at.lon, lat: at.lat, zoom: clampZoom(view.zoom * 2) } };
}

/** Rings ([[lon, lat], ...]) from a GeoJSON (Multi)Polygon feature collection. */
export function ringsOf(geo) {
  const out = [];
  const feats = geo.type === "FeatureCollection" ? geo.features : [geo];
  for (const f of feats) {
    const g = f.geometry || f;
    if (g.type === "Polygon") out.push(...g.coordinates);
    else if (g.type === "MultiPolygon") for (const poly of g.coordinates) out.push(...poly);
  }
  return out;
}

// ---------------------------------------------------------------- the LTMS pyramid on the frame edge

/** An arrow pointing from the frame centre toward (dx, dy), in eight directions. */
export function arrowFor(dx, dy) {
  const arrows = ["→", "↘", "↓", "↙", "←", "↖", "↑", "↗"];
  const a = Math.atan2(dy, dx); // screen y grows downward
  const i = ((Math.round(a / (Math.PI / 4)) % 8) + 8) % 8;
  return arrows[i];
}

/**
 * Keep the pyramid in the frame: a point inside the frame (at least `margin`
 * from every edge) and not hidden behind the globe stays where it is;
 * otherwise it is pinned to the frame edge on the line from the centre toward
 * it. Returns {x, y, edge, arrow}; `arrow` points the way to the real place.
 */
export function pinToEdge(p, W, H, margin = 46, hidden = false) {
  const inside = p.x >= margin && p.x <= W - margin && p.y >= margin && p.y <= H - margin;
  if (inside && !hidden) return { x: p.x, y: p.y, edge: false, arrow: null };
  let dx = p.x - W / 2;
  let dy = p.y - H / 2;
  if (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9) dy = -1; // straight behind: pin to the top
  const k = Math.min((W / 2 - margin) / Math.abs(dx || 1e-9), (H / 2 - margin) / Math.abs(dy || 1e-9));
  return { x: W / 2 + dx * k, y: H / 2 + dy * k, edge: true, arrow: arrowFor(dx, dy) };
}

// ---------------------------------------------------------------- camera

/** Ease in-out (quadratic), 0..1. */
export function easeInOut(u) {
  const t = Math.min(1, Math.max(0, u));
  return t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
}

function wrapLon(lon) {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

/**
 * The globe's camera: drag, zoom, eased moves and the idle drift. Pure apart
 * from its own state; pass wall times in (ms).
 *   drag(dx, dy, R, now)    turn the globe by a pointer move of dx, dy px
 *   zoomBy(f, now)          multiply the zoom (wheel, pinch), clamped
 *   moveTo(target, now, {instant})  ease to {lon?, lat?, zoom?} over EASE_MS
 *   step(now, {animate})    advance a move, or drift 0.3°/s after 20 s idle
 *   view                    {lon, lat, zoom}
 */
export function createGlobeCamera(initial = {}, now = 0) {
  let view = { lon: initial.lon ?? 0, lat: initial.lat ?? 20, zoom: clampZoom(initial.zoom ?? 1) };
  let move = null;
  let lastInput = now;
  let lastStep = now;
  return {
    get view() {
      return view;
    },
    get moving() {
      return move != null;
    },
    get lastInput() {
      return lastInput;
    },
    input(at) {
      lastInput = at;
    },
    drag(dx, dy, R, at) {
      lastInput = at;
      move = null;
      const k = 180 / (Math.PI * Math.max(1, R));
      view = { ...view, lon: wrapLon(view.lon - dx * k), lat: Math.max(-85, Math.min(85, view.lat + dy * k)) };
    },
    zoomBy(f, at) {
      lastInput = at;
      move = null;
      view = { ...view, zoom: clampZoom(view.zoom * f) };
    },
    setView(v, at) {
      lastInput = at;
      move = null;
      view = { lon: wrapLon(v.lon ?? view.lon), lat: v.lat ?? view.lat, zoom: clampZoom(v.zoom ?? view.zoom) };
    },
    moveTo(target, at, { instant = false } = {}) {
      lastInput = at;
      const to = {
        lon: target.lon ?? view.lon, lat: target.lat ?? view.lat, zoom: clampZoom(target.zoom ?? view.zoom),
      };
      if (instant) {
        view = { ...to, lon: wrapLon(to.lon) };
        move = null;
        return;
      }
      // Turn the short way round.
      let dLon = wrapLon(to.lon - view.lon);
      if (dLon === -180) dLon = 180;
      move = { t0: at, from: view, to: { ...to, lon: view.lon + dLon } };
    },
    step(at, { animate = true } = {}) {
      const dt = Math.max(0, at - lastStep);
      lastStep = at;
      if (move) {
        const u = (at - move.t0) / EASE_MS;
        const e = easeInOut(u);
        const { from, to } = move;
        view = {
          lon: wrapLon(from.lon + (to.lon - from.lon) * e),
          lat: from.lat + (to.lat - from.lat) * e,
          // Zoom eases on a log scale, so each doubling takes the same time.
          zoom: clampZoom(from.zoom * (to.zoom / from.zoom) ** e),
        };
        if (u >= 1) move = null;
        lastInput = at;
      } else if (animate && at - lastInput >= IDLE_MS && view.zoom < SYSTEM_FADE[0]) {
        view = { ...view, lon: wrapLon(view.lon - (DRIFT_DEG_PER_S * dt) / 1000) };
      }
      return view;
    },
  };
}
