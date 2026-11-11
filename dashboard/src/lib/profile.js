// Pure helpers for the member and swarm profile pages (issues #192-#198).
import { CATEGORIES } from "./visual.js";

export const RANGES = [
  { key: "24h", label: "24h", hours: 24 },
  { key: "7d", label: "7d", hours: 168 },
  { key: "30d", label: "30d", hours: 720 },
  { key: "all", label: "All", hours: 0 },
];
export const DEFAULT_RANGE = "7d";

export const TABS = [
  { key: "overview", label: "Overview" },
  { key: "places", label: "Places & movement" },
  { key: "interactions", label: "Interactions" },
  { key: "network", label: "Network" },
  { key: "activity", label: "Activity" },
  { key: "senses", label: "Senses" },
  { key: "history", label: "History" },
];
export const DEFAULT_TAB = "overview";

export function rangeOf(param) {
  return RANGES.find((r) => r.key === param) ?? RANGES.find((r) => r.key === DEFAULT_RANGE);
}

export function tabOf(param) {
  return TABS.find((t) => t.key === param)?.key ?? DEFAULT_TAB;
}

/** The tab after `current` in `direction` (+1 right, -1 left), wrapping; Home/End jump. */
export function nextTab(current, key) {
  const i = TABS.findIndex((t) => t.key === current);
  if (key === "Home") return TABS[0].key;
  if (key === "End") return TABS[TABS.length - 1].key;
  const step = key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0;
  return TABS[(i + step + TABS.length) % TABS.length].key;
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
