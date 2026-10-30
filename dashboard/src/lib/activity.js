// Record-type colors and pure helpers for the activity view (issue #74).
// Colors are the Okabe-Ito colorblind-safe palette, each checked for
// >= 3:1 contrast on the page background #0F1828 (WCAG 1.4.11).
// Types are grouped by the sense -> think -> act -> learn cycle so the
// legend reads as behavior.

export const TYPE_GROUPS = [
  { group: "Sense", types: ["perception"] },
  { group: "Think", types: ["thought", "decision"] },
  { group: "Act", types: ["action"] },
  { group: "Social", types: ["communication"] },
  { group: "Learn & govern", types: ["lesson", "policy"] },
  { group: "Other", types: ["event", "system"] },
];

export const TYPE_COLORS = {
  perception: "#56B4E9",
  thought: "#CC79A7",
  decision: "#F0E442",
  action: "#E69F00",
  communication: "#009E73",
  lesson: "#0072B2",
  policy: "#D55E00",
  event: "#A0A8B8",
  system: "#C9CED8",
};

export const TYPE_ORDER = TYPE_GROUPS.flatMap((g) => g.types);

const UNKNOWN_COLOR = "#93A0B8";

export function typeColor(type) {
  return TYPE_COLORS[type] ?? UNKNOWN_COLOR;
}

export function trend(total, previous) {
  const pct = previous ? Math.round(((total - previous) / previous) * 100) : null;
  let direction;
  if (previous === 0) {
    direction = total > 0 ? "new" : "flat";
  } else if (Math.abs(pct) < 10) {
    direction = "flat";
  } else {
    direction = pct > 0 ? "up" : "down";
  }
  let label;
  if (direction === "new") {
    label = "new activity";
  } else if (direction === "flat") {
    label = "about the same";
  } else {
    label = `${direction} ${Math.abs(pct)}% vs previous window`;
  }
  return { pct, direction, label };
}

export function isSurge(total, previous) {
  return previous >= 5 && total >= 2 * previous;
}

export function stack(byType) {
  return TYPE_ORDER.map((type) => {
    const count = byType[type] ?? 0;
    if (count === 0) return null;
    return { type, count, color: typeColor(type) };
  }).filter((s) => s !== null);
}
