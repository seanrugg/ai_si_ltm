// Fleet health (#381): pure helpers for the Health page. Rows come from
// GET /api/admin/health (server/services/fleet_health.py).

/** Status in words; colour is only extra (508). */
export const STATUS_TEXT = {
  on_time: "On time",
  late: "Late",
  silent: "Silent",
  unknown: "No interval yet",
  never: "Never heard",
  idle: "Idle (runs when used)",
};

const STATUS_ORDER = { silent: 0, late: 1, never: 2, unknown: 3, idle: 4, on_time: 5 };

/** Tailwind text class for a status: colour as extra to the words. */
export function statusClass(status) {
  if (status === "silent") return "text-bad";
  if (status === "late") return "text-warn";
  if (status === "on_time") return "text-ok";
  return "text-paper-muted";
}

const UP = new Set(["active", "up", "running", "ok"]);

function span(s) {
  if (s == null) return null;
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

/** "every 12 h" for an interval in seconds, or null. */
export function intervalText(seconds) {
  const s = span(seconds);
  return s ? `every ${s}` : null;
}

/**
 * One line from the latest health self-report, e.g.
 * "GPU 71 °C, 1 throttle event in 24 h, wire-pod up". Null when never reported.
 */
export function healthSummary(health) {
  if (!health) return null;
  const p = health.payload || {};
  const parts = [];
  const zones = Object.entries(p.temp_c || {})
    .map(([zone, t]) => [zone, typeof t === "number" ? t : t?.last])
    .filter(([, v]) => typeof v === "number");
  if (zones.length) {
    const [zone, v] = zones.reduce((a, b) => (b[1] > a[1] ? b : a));
    parts.push(`${zone} ${Math.round(v)} °C`);
  }
  const n = health.throttle_events_24h || 0;
  if (n) parts.push(`${n} throttle event${n === 1 ? "" : "s"} in 24 h`);
  for (const [name, state] of Object.entries(p.services || {}).sort()) {
    parts.push(`${name} ${UP.has(String(state).toLowerCase()) ? "up" : String(state)}`);
  }
  return parts.length ? parts.join(", ") : (health.summary || "Reported");
}

/** True when the latest health report has something to look at. */
export function hasHealthWarning(health) {
  if (!health) return false;
  const p = health.payload || {};
  if ((health.throttle_events_24h || 0) > 0 || p.throttled) return true;
  return Object.values(p.services || {}).some((s) => !UP.has(String(s).toLowerCase()));
}

export const FILTERS = [
  { key: "all", label: "All" },
  { key: "late", label: "Late or silent" },
  { key: "health", label: "Health warnings" },
];

/** Rows kept by the filter ("all" | "late" | "health") and swarm ("" for every swarm, "__free" for free agents). */
export function filterRows(rows, filter = "all", swarm = "") {
  return (rows || []).filter((r) => {
    if (filter === "late" && r.status !== "late" && r.status !== "silent") return false;
    if (filter === "health" && !hasHealthWarning(r.health)) return false;
    if (swarm === FREE) return !r.swarm;
    if (swarm && r.swarm !== swarm) return false;
    return true;
  });
}

export const FREE = "__free";

export const SORTS = [
  { key: "name", label: "Name" },
  { key: "heard", label: "Last heard" },
  { key: "status", label: "Status" },
];

const byName = (a, b) => a.name.localeCompare(b.name);

/** Sorted copy: by name, by last heard (newest first, never last) or by status (worst first). */
export function sortRows(rows, sort = "name") {
  const out = [...(rows || [])];
  if (sort === "heard") {
    out.sort((a, b) => (b.last_heard || "").localeCompare(a.last_heard || "") || byName(a, b));
  } else if (sort === "status") {
    out.sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || byName(a, b));
  } else {
    out.sort(byName);
  }
  return out;
}

/** [{swarm, rows}] grouped by swarm (by name), free agents last as swarm null. */
export function groupRows(rows) {
  const groups = new Map();
  for (const r of rows || []) {
    const k = r.swarm || null;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === null) - (b === null) || String(a).localeCompare(String(b)))
    .map(([swarm, list]) => ({ swarm, rows: list }));
}
