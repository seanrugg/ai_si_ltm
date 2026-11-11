// Orthographic globe math (issue #195). Pure functions, no map library.
// A view is { lon, lat, zoom }: the point at the centre of the disc, and zoom.
const RAD = Math.PI / 180;
export const MIN_ZOOM = 1;
export const MAX_ZOOM = 8;
export const HOME_ZOOM = 6;
export const HOME_RADIUS_DEG = 6;

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

const pt = (p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`;

/**
 * Rings of [lon, lat] to SVG paths: `fill` holds rings that are fully
 * visible (closed); `stroke` holds the visible runs of partly hidden rings.
 */
export function pathFor(rings, rot, R, cx, cy) {
  let fill = "";
  let stroke = "";
  for (const ring of rings) {
    const pts = ring.map(([lon, lat]) => project(lon, lat, rot, R, cx, cy));
    if (pts.every((p) => p.visible)) {
      fill += `M${pts.map(pt).join("L")}Z`;
    } else {
      for (const run of runs(pts)) if (run.length > 1) stroke += `M${run.map(pt).join("L")}`;
    }
  }
  return { fill, stroke };
}

/** Meridians and parallels every `step` degrees, as one stroke path. */
export function graticule(rot, R, cx, cy, step = 30) {
  const lines = [];
  for (let lon = -180; lon < 180; lon += step) {
    lines.push(Array.from({ length: 37 }, (_, i) => [lon, -90 + i * 5]));
  }
  for (let lat = -60; lat <= 60; lat += step) {
    lines.push(Array.from({ length: 73 }, (_, i) => [-180 + i * 5, lat]));
  }
  let d = "";
  for (const line of lines) {
    for (const run of runs(line.map(([lon, lat]) => project(lon, lat, rot, R, cx, cy)))) {
      if (run.length > 1) d += `M${run.map(pt).join("L")}`;
    }
  }
  return d;
}

function toVec(lon, lat) {
  const l = lon * RAD;
  const p = lat * RAD;
  return [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)];
}

/**
 * A great-circle arc from a to b ({lon, lat}), raised above the surface:
 * points { lon, lat, alt } with alt = lift * sin(pi * t) (a fraction of R).
 */
export function arc(a, b, lift = 0.15, steps = 32) {
  const va = toVec(a.lon, a.lat);
  const vb = toVec(b.lon, b.lat);
  const dot = Math.min(1, Math.max(-1, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2]));
  const omega = Math.acos(dot);
  return Array.from({ length: steps + 1 }, (_, i) => {
    const t = i / steps;
    let v;
    if (omega < 1e-6) v = va;
    else {
      const s1 = Math.sin((1 - t) * omega) / Math.sin(omega);
      const s2 = Math.sin(t * omega) / Math.sin(omega);
      v = [0, 1, 2].map((k) => s1 * va[k] + s2 * vb[k]);
    }
    return { lon: Math.atan2(v[1], v[0]) / RAD, lat: Math.asin(Math.max(-1, Math.min(1, v[2]))) / RAD, alt: lift * Math.sin(Math.PI * t) };
  });
}

/** The visible part of an arc as an SVG path (raised points project at R * (1 + alt)). */
export function arcPath(points, rot, R, cx, cy) {
  const pts = points.map((p) => {
    const q = project(p.lon, p.lat, rot, R * (1 + p.alt), cx, cy);
    return { ...q, visible: project(p.lon, p.lat, rot, R, cx, cy).visible || p.alt > 0.05 };
  });
  return runs(pts).filter((r) => r.length > 1).map((r) => `M${r.map(pt).join("L")}`).join("");
}

export function clampZoom(z) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

/** Degrees between two lon/lat points along the surface. */
export function distanceDeg(a, b) {
  const va = toVec(a.lon, a.lat);
  const vb = toVec(b.lon, b.lat);
  return Math.acos(Math.min(1, Math.max(-1, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2]))) / RAD;
}

/**
 * Double-click: centre on the clicked point and double the zoom (max 8). At
 * zoom >= 6, a double-click within 6 degrees of `home` asks for the Home view.
 * Returns { view, home: boolean } or null when the click is off the disc.
 */
export function zoomAt(sx, sy, view, R, cx, cy, home = null) {
  const at = invert(sx, sy, view, R, cx, cy);
  if (!at) return null;
  if (home && view.zoom >= HOME_ZOOM && distanceDeg(at, home) <= HOME_RADIUS_DEG) {
    return { view, home: true };
  }
  return { view: { lon: at.lon, lat: at.lat, zoom: clampZoom(view.zoom * 2) }, home: false };
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
