// The Growth tab (#343): pure helpers over the growth ledger (#342).

/** The ledger's `days` for a profile range: "All" asks for the server's maximum, a year. */
export function daysFor(range) {
  if (!range || !range.hours) return 365;
  return Math.max(1, Math.ceil(range.hours / 24));
}

/** "0.042%" for a learning ratio, or "—" when nothing was written. */
export function ratioText(ratio) {
  if (ratio == null) return "—";
  const pct = ratio * 100;
  return `${pct < 0.01 && pct > 0 ? pct.toFixed(4) : pct < 1 ? pct.toFixed(3) : pct.toFixed(1)}%`;
}

/** The headline numbers, in reading order. `energy` only for members that have it. */
export function headlines(g) {
  const L = g.lessons || {};
  const P = g.predictions || {};
  const items = [
    { key: "verified", label: "Lessons verified by another model family", value: L.verified ?? 0 },
    { key: "refuted", label: "Lessons refuted by another model family", value: L.refuted ?? 0 },
    { key: "unreviewed", label: "Lessons not yet reviewed", value: L.unreviewed ?? 0 },
    { key: "ratio", label: "Learning ratio (verified lessons per record written)", value: ratioText(g.learning_ratio) },
    { key: "brier", label: "Prediction calibration (Brier score, lower is better)",
      value: P.brier == null ? "—" : P.brier.toFixed(3) },
    { key: "answered", label: "Questions answered by others",
      value: `${g.questions?.answered_by_others ?? 0} of ${g.questions?.asked ?? 0}` },
    { key: "studied", label: "Lessons from study (citing a dataset)", value: L.studied ?? 0 },
    { key: "directives", label: "Directives done / couldn't",
      value: `${g.directives?.done ?? 0} / ${g.directives?.couldnt ?? 0}` },
  ];
  if (g.energy) {
    const e = g.energy;
    items.push({ key: "energy", label: "Off-dock outcomes (docked / rescued / powered off)",
                 value: `${e.docked ?? 0} / ${e.rescued ?? 0} / ${e.powered_off ?? 0}` });
  }
  return items;
}

/** The trend as rows, oldest first: one per day with records written and lessons verified. */
export function trendRows(daily) {
  return Object.entries(daily || {}).map(([day, d]) => ({
    day, records: d.records ?? 0, lessons: d.lessons ?? 0, verified: d.verified ?? 0,
  }));
}

export const STATUS_TEXT = {
  verified: "Verified by another family",
  refuted: "Refuted by another family",
  unsure: "Unsure (another family)",
  unreviewed: "Not reviewed yet",
};

/** "bad_ref 2 · duplicate 1", or "none". */
export function reasonsText(counts) {
  const parts = Object.entries(counts || {}).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`);
  return parts.length ? parts.join(" · ") : "none";
}

export const BUDGET_FIELDS = [
  ["calls_per_wake", "Brain calls per wake"],
  ["tokens_per_wake", "Tokens per wake"],
  ["writes_per_day", "Records written per day"],
];

/** The PATCH body for the budget form: blank means "the device's own setting" (null). */
export function budgetPatch(draft) {
  const budget = {};
  for (const [k] of BUDGET_FIELDS) {
    const raw = (draft[k] ?? "").toString().trim();
    if (raw === "") budget[k] = null;
    else {
      const n = Number(raw);
      if (!Number.isInteger(n) || n < 0) return { error: `${k.replaceAll("_", " ")} must be a whole number from 0` };
      budget[k] = n;
    }
  }
  return { body: { budget } };
}
