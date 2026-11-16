// The dashboard's visual language (issue #184):
// ring color = swarm role, icon = member kind, line color + dash = data category.
// Profile tabs and, later, the rest of the dashboard draw from this one place.

export const ROLES = {
  hub: { color: "#b8a8ff", label: "Hub" },
  member: { color: "#00e5ff", label: "Member" },
  observer: { color: "#ffd166", label: "Observer" },
  free: { color: "#8aa4c2", label: "Free agent" },
  // The swarm leader (#309): directs the swarm; shown alongside the member's swarm role.
  leader: { color: "#ff8fb1", label: "Leader" },
};

export const KINDS = {
  physical: { label: "Physical AI", note: "body, on-board AI" },
  hybrid: { label: "Hybrid", note: "body, off-board AI" },
  agent: { label: "AI agent", note: "software only" },
  hub: { label: "Hub", note: "relays for its swarm" },
};

// Each category has its own color AND dash, so meaning never rests on color alone.
export const CATEGORIES = [
  { key: "sense", label: "Sensing", types: ["perception"], color: "#00ff9d", dash: "2 6", direction: "member → FLTMS" },
  { key: "act", label: "Deciding & acting", types: ["thought", "decision", "action", "event"], color: "#ff8a5c", dash: "8 6", direction: "member → FLTMS" },
  { key: "comm", label: "Communicating", types: ["communication"], color: "#f2f6ff", dash: "14 6", direction: "between members" },
  { key: "learn", label: "Learning & rules", types: ["lesson", "policy"], color: "#ff5fd2", dash: "10 4 2 4", direction: "policies FLTMS → member" },
];

export const NEUTRAL = "#e2eaf5"; // places, admin events, wipes: anything that is neither a role nor a category
export const RELAY = "#5d7a99"; // relay paths, and orbits (Holo Light)

// Holo Light (#400, docs/holo-light.md). Meaning colors: like the ones above,
// they never change with the theme.
export const CAT_SELF = "#6aa8ff"; // self-reports (heartbeat, health), drawn as the member's heartbeat
export const LTMS_CORE = "#9fdcff"; // the LTMS pyramid
export const MIND = "#b06bff"; // a model thinking
export const VOID = "#000"; // the visualization stage, only inside a VizFrame

/** The Holo Light colors by token name (mirrored as CSS variables in src/index.css). */
export const LIGHT_COLORS = {
  "cat-self": CAT_SELF,
  "ltms-core": LTMS_CORE,
  mind: MIND,
  relay: RELAY,
  void: VOID,
};

/**
 * The brightness ladder (opacities). Brightness is meaning:
 * asleep = not available or future; trail = history; breathing = present and
 * waiting (a slow min..max cycle over periodMs, resting at `rest`); lit = past,
 * found, steady; live = happening now.
 */
export const LIGHT = {
  asleep: 0.18,
  trail: 0.35,
  breathing: { min: 0.45, max: 0.75, rest: 0.6, periodMs: 3200 },
  lit: 0.85,
  live: 1.0,
};

const CATEGORY_BY_TYPE = Object.fromEntries(CATEGORIES.flatMap((c) => c.types.map((t) => [t, c.key])));

/** A perception tagged `telemetry`: data from one of the member's sensors. */
export function isSensed(type, tags) {
  return type === "perception" && Array.isArray(tags) && tags.includes("telemetry");
}

/** "sense" | "act" | "comm" | "learn", or null (for `system` and unknown types). */
// eslint-disable-next-line no-unused-vars
export function categoryOf(type, tags) {
  // Sensed and perceived perceptions are both Sensing; `tags` only splits them
  // for display (see isSensed).
  return CATEGORY_BY_TYPE[type] ?? null;
}

export function category(key) {
  return CATEGORIES.find((c) => c.key === key) ?? null;
}

/** The member's swarm role (hub, member, observer), else "free". */
export function roleOf(member) {
  const role = member?.role ?? member?.membership?.role ?? null;
  return role && ROLES[role] && role !== "free" ? role : "free";
}

/**
 * "physical" | "hybrid" | "agent" | "hub".
 * A swarm hub or a relay is a hub; agents and assistants are software only;
 * a robot is hybrid when its AI runs off board (resolved cognition, #185).
 */
export function kindOf(member) {
  if (roleOf(member) === "hub" || member?.kind === "relay") return "hub";
  if (member?.kind === "agent" || member?.kind === "assistant") return "agent";
  if (member?.cognition?.value === "offboard") return "hybrid";
  return "physical";
}
