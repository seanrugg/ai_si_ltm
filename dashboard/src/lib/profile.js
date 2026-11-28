// Pure helpers for the member and swarm profile pages (issues #192-#198).
import { CATEGORIES, kindOf } from "./visual.js";

export const RANGES = [
  { key: "24h", label: "24h", hours: 24 },
  { key: "7d", label: "7d", hours: 168 },
  { key: "30d", label: "30d", hours: 720 },
  { key: "all", label: "All", hours: 0 },
];
export const DEFAULT_RANGE = "7d";

export const TABS = [
  { key: "overview", label: "Overview" },
  { key: "specs", label: "Specs", memberOnly: true },
  { key: "members", label: "Members", swarmOnly: true },
  // #405: Geography was Network, and Places & movement folds into it (?tab=places opens it zoomed in).
  { key: "geography", label: "Geography", aliases: ["network", "places"] },
  { key: "topology", label: "Topology", aliases: ["interactions"] }, // #404: was Interactions
  { key: "chronology", label: "Chronology", aliases: ["activity"] }, // #403: was Activity
  { key: "senses", label: "Senses" },
  { key: "growth", label: "Growth" }, // #343: the growth ledger (#342)
  { key: "history", label: "History" },
  { key: "manage", label: "Manage", swarmOnly: true },
];
export const DEFAULT_TAB = "overview";

export function rangeOf(param) {
  return RANGES.find((r) => r.key === param) ?? RANGES.find((r) => r.key === DEFAULT_RANGE);
}

/** The tabs a subject has: Members is for swarm profiles only (#282), Specs for members (#333). */
export function tabsFor(subject) {
  return TABS.filter((t) => (!t.swarmOnly || subject === "swarm") && (!t.memberOnly || subject === "member"));
}

/** The tab for a `?tab=` value; old keys (`aliases`) keep working. */
export function tabOf(param, subject) {
  return tabsFor(subject).find((t) => t.key === param || t.aliases?.includes(param))?.key ?? DEFAULT_TAB;
}

/** The tab after `current` in `direction` (+1 right, -1 left), wrapping; Home/End jump. */
export function nextTab(current, key, subject) {
  const tabs = tabsFor(subject);
  const i = tabs.findIndex((t) => t.key === current);
  if (key === "Home") return tabs[0].key;
  if (key === "End") return tabs[tabs.length - 1].key;
  const step = key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0;
  return tabs[(i + step + tabs.length) % tabs.length].key;
}

/** "1 hub · 2 members · 1 observer" (zero parts left out), or "no members yet" (#282). */
export function swarmCount(members) {
  const n = (role) => (members || []).filter((m) => m.role === role).length;
  const parts = [["hub", "hubs"], ["member", "members"], ["observer", "observers"]]
    .map(([one, many]) => [n(one), one, many]).filter(([c]) => c > 0)
    .map(([c, one, many]) => `${c} ${c === 1 ? one : many}`);
  return parts.length ? parts.join(" · ") : "no members yet";
}

/**
 * Stack one `daily` entry by category, in category order:
 * [{ key, y0, y1, value }], where y1 of the last segment is the day's total.
 */
export function stackDay(day) {
  let y = 0;
  return CATEGORIES.map((c) => {
    const value = Number(day[c.key] || 0);
    const seg = { key: c.key, y0: y, y1: y + value, value };
    y += value;
    return seg;
  });
}

export function dayTotal(day) {
  return CATEGORIES.reduce((sum, c) => sum + Number(day[c.key] || 0), 0);
}

/** "Nice" axis maximum: 1, 2 or 5 times a power of ten, at least the max. */
export function niceMax(max) {
  if (!(max > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(max));
  return [1, 2, 5, 10].map((m) => m * p).find((v) => v >= max);
}

/**
 * Whole-number y-axis for count charts (#385): `{ max, ticks }`.
 * A maximum of 0–4 gets a tick at every whole number (0, 1, … max, at least 0, 1).
 * Above that, the step is the smallest 1, 2 or 5 times a power of ten that needs
 * at most four intervals, and the axis top is the next multiple of that step.
 * Ticks are always distinct whole numbers, so labels never repeat.
 */
export function countTicks(max) {
  const top = Math.ceil(Number(max) > 0 ? Number(max) : 0);
  if (top <= 4) {
    const end = Math.max(1, top);
    return { max: end, ticks: Array.from({ length: end + 1 }, (_, i) => i) };
  }
  let step = 1;
  for (let p = 1; ; p *= 10) {
    const found = [1, 2, 5].map((m) => m * p).find((s) => Math.ceil(top / s) <= 4);
    if (found) { step = found; break; }
  }
  const end = Math.ceil(top / step) * step;
  return { max: end, ticks: Array.from({ length: end / step + 1 }, (_, i) => i * step) };
}

/** Index of the daily bucket containing `ts` (buckets are sorted ISO days), or -1. */
export function bucketIndex(daily, ts) {
  const day = String(ts).slice(0, 10);
  let at = -1;
  daily.forEach((d, i) => {
    if (d.day <= day) at = i;
  });
  return at;
}

/** Memory wipes from #188's history events: [{ ts, generation }]. */
export function wipesOf(events) {
  return (events || [])
    .filter((e) => e.action === "memory_wipe")
    .map((e) => ({ ts: e.ts, generation: Number(String(e.detail ?? "").match(/\d+/)?.[0]) || null }));
}

export function percent(share) {
  return share == null ? null : `${Math.round(share * 100)}%`;
}

/**
 * Fit points ({x, y} in a frame's units) into a width × height viewBox with
 * `pad` (a fraction) of padding on each side, keeping one scale for both axes
 * so distances look right. Screen y grows downward, so frame y is flipped.
 * Returns { map(p) -> {x, y}, bounds }.
 */
export function makeScaler(points, width, height, pad = 0.1) {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  let minX = Math.min(...xs);
  let maxX = Math.max(...xs);
  let minY = Math.min(...ys);
  let maxY = Math.max(...ys);
  if (!points.length) {
    minX = minY = 0;
    maxX = maxY = 1;
  }
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const innerW = width * (1 - 2 * pad);
  const innerH = height * (1 - 2 * pad);
  const k = Math.min(innerW / spanX, innerH / spanY);
  const offX = width * pad + (innerW - spanX * k) / 2;
  const offY = height * pad + (innerH - spanY * k) / 2;
  return {
    map: (p) => ({ x: offX + (p.x - minX) * k, y: height - (offY + (p.y - minY) * k) }),
    bounds: { minX, maxX, minY, maxY },
  };
}

/** Pose samples from sensed records (`payload.kind == "pose"`), oldest first. */
export function poseSamples(records) {
  const out = [];
  for (const r of records || []) {
    const p = r.payload;
    if (!p || p.kind !== "pose") continue;
    for (const s of p.samples || []) {
      const x = s.x ?? s.v?.x;
      const y = s.y ?? s.v?.y;
      if (typeof x === "number" && typeof y === "number") {
        const t = typeof s.t === "number" ? s.t : 0;
        out.push({ agent: r.agent, ts: new Date(Date.parse(r.ts) + t * 1000).toISOString(), x, y, frame: p.frame || "frame" });
      }
    }
  }
  return out.sort((a, b) => a.ts.localeCompare(b.ts));
}

/**
 * A member with a body (#407): Physical AI, or Hybrid with `kind robot`.
 * AI agents and hubs have no body, so they get no Choreography.
 */
export function hasBody(member) {
  if (!member) return false;
  const k = kindOf(member);
  return k === "physical" || (k === "hybrid" && member.kind === "robot");
}

/**
 * Pose tracks (#407): one per member and frame, newest first. A frame is the
 * member's own (e.g. odometry that restarts when the robot is picked up), so
 * two frames are never joined. Each track's samples are oldest first;
 * `newFrame` is true when the member had an older frame before this one.
 */
export function poseFrames(records) {
  const byKey = new Map();
  for (const r of records || []) {
    const p = r.payload;
    if (!p || p.kind !== "pose") continue;
    const frame = p.frame || "frame";
    const key = `${r.agent}\u0000${frame}`;
    if (!byKey.has(key)) byKey.set(key, { key, agent: r.agent, frame, unit: p.unit || null, samples: [] });
    const track = byKey.get(key);
    for (const s of p.samples || []) {
      const x = s.x ?? s.v?.x;
      const y = s.y ?? s.v?.y;
      if (typeof x !== "number" || typeof y !== "number") continue;
      const t = typeof s.t === "number" ? s.t : 0;
      track.samples.push({ ts: new Date(Date.parse(r.ts) + t * 1000).toISOString(), x, y });
    }
  }
  const tracks = [...byKey.values()].filter((t) => t.samples.length);
  for (const t of tracks) {
    t.samples.sort((a, b) => a.ts.localeCompare(b.ts));
    t.start = t.samples[0].ts;
    t.end = t.samples[t.samples.length - 1].ts;
  }
  tracks.sort((a, b) => b.end.localeCompare(a.end));
  for (const t of tracks) t.newFrame = tracks.some((o) => o !== t && o.agent === t.agent && o.end <= t.start);
  return tracks;
}

/** A round scale-bar length (1, 2 or 5 × 10^n) of about `target` data units. */
export function niceLength(target) {
  if (!(target > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(target));
  const m = target / p;
  return (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p;
}

/** Action records whose payload carries a pose ({pose: {x, y}} or top-level x, y). */
export function actionPoses(records) {
  const out = [];
  for (const r of records || []) {
    const p = r.payload || {};
    const x = p.pose?.x ?? p.x;
    const y = p.pose?.y ?? p.y;
    if (typeof x === "number" && typeof y === "number") {
      const frame = p.pose?.frame ?? p.frame;
      out.push({ agent: r.agent, ts: r.ts, x, y, outcome: r.outcome, summary: r.summary, ...(frame ? { frame } : {}) });
    }
  }
  return out;
}

// History markers (issue #197): color by kind, icon by action.
export const HISTORY_ICONS = {
  enrolled: "M12 5v14 M5 12h14",
  joined: "M10 6a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M3 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M17 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M11 7.8 6 16.2 M13 7.8l5 8.4 M7 18h10",
  lesson: "M4 6.5C6 5.5 9 5.5 12 7c3-1.5 6-1.5 8-.5V18c-2-1-5-1-8 .5-3-1.5-6-1.5-8-.5z M12 7v11.5",
  network: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M3 12h18 M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9 M12 3c-2.5 2.5-3.8 5.5-3.8 9s1.3 6.5 3.8 9",
  wipe: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M5.6 5.6l12.8 12.8",
  deletion: "M5 7h14 M10 7V4h4v3 M7 7l1 13h8l1-13",
  other: "M12 8v4 M12 16h.01 M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0",
};

const ICON_FOR_ACTION = {
  enroll_request: "enrolled", enroll_auto: "enrolled", enroll_approve: "enrolled",
  swarm_join: "joined", swarm_leave: "joined",
  swarm_appoint_hub: "joined", swarm_appoint_hub_replaced: "joined",
  first_lesson: "lesson",
  network_change: "network",
  memory_wipe: "wipe",
  record_delete: "deletion",
};

/** { color, icon, iconPath } for one history event. */
export function historyMarker(event, { ROLES, NEUTRAL, learnColor }) {
  const icon = ICON_FOR_ACTION[event.action] || "other";
  let color = NEUTRAL;
  if (event.kind === "learning") color = learnColor;
  else if (event.kind === "membership") {
    const role = event.action === "swarm_appoint_hub" ? "hub"
      : event.action === "swarm_join" ? String(event.detail || "").match(/as (\w+)/)?.[1] : null;
    color = (ROLES[role] || ROLES.free).color;
  }
  return { color, icon, iconPath: HISTORY_ICONS[icon] };
}

/** Points for a sparkline of `series` ([{t, v}]) in a width × height box. */
export function sparkPoints(series, width, height) {
  if (!series?.length) return "";
  const vs = series.map((p) => p.v);
  const min = Math.min(...vs);
  const span = Math.max(...vs) - min || 1;
  const step = series.length > 1 ? width / (series.length - 1) : 0;
  return series.map((p, i) => `${(i * step).toFixed(1)},${(height - 1 - ((p.v - min) / span) * (height - 2)).toFixed(1)}`).join(" ");
}
