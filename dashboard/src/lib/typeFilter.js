// Grouped record types for the timeline's Types control (issue #181).
// The four groups match the dashboard's data categories (#184).

export const GROUPS = [
  { label: "Sensing", types: ["sensed", "perception"] },
  { label: "Deciding & acting", types: ["thought", "decision", "action", "event"] },
  { label: "Communicating", types: ["communication"] },
  { label: "Learning & rules", types: ["lesson", "policy", "system"] },
];

export const LABELS = {
  sensed: "sensor data",
  perception: "perceptions",
  thought: "thoughts",
  decision: "decisions",
  action: "actions",
  event: "events",
  communication: "communications",
  lesson: "lessons",
  policy: "policies",
  system: "system",
};

export const ALL_TYPES = GROUPS.flatMap((g) => g.types);

// Today's default: everything except `sensed` (telemetry hidden).
export const DEFAULT_TYPES = ALL_TYPES.filter((t) => t !== "sensed");

/**
 * The `type` query param as a Set of filter types.
 * Empty or missing means the default: everything except `sensed`;
 * "none" means nothing selected (issue #205).
 * Unknown names are dropped, so a stale URL never breaks the page.
 */
export const NONE = "none";

export function parseTypes(param) {
  if (String(param ?? "").trim() === NONE) return new Set();
  const list = String(param ?? "")
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t && LABELS[t] !== undefined);
  return list.length > 0 ? new Set(list) : new Set(DEFAULT_TYPES);
}

/**
 * A Set of filter types as the `type` query param: a comma list in group
 * order, "" when the set is the default (so default views stay bookmarkable),
 * or "none" when nothing is selected.
 */
export function serializeTypes(set) {
  // "" already means the default, so the empty selection needs its own value.
  if (set.size === 0) return NONE;
  if (set.size === DEFAULT_TYPES.length && DEFAULT_TYPES.every((t) => set.has(t))) return "";
  return ALL_TYPES.filter((t) => set.has(t)).join(",");
}

/** A short name for the picker button and the filter chip. */
export function summary(set) {
  if (set.size === 0) return "No types";
  if (set.size === ALL_TYPES.length && ALL_TYPES.every((t) => set.has(t))) return "All types";
  if (set.size === DEFAULT_TYPES.length && DEFAULT_TYPES.every((t) => set.has(t))) return "All except sensor data";
  if (set.size === 1) return LABELS[[...set][0]];
  return `${set.size} types`;
}
