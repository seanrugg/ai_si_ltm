// Formatting helpers shared by every page. Pure functions (tested in format.test.js).

export function shortHash(hex, n = 10) {
  return hex ? `${hex.slice(0, n)}…` : "";
}

const RTF = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const STEPS = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 30],
  ["month", 12],
  ["year", Infinity],
];

export function relativeTime(iso, now = Date.now()) {
  if (!iso) return "";
  let delta = (new Date(iso).getTime() - now) / 1000;
  for (const [unit, size] of STEPS) {
    if (Math.abs(delta) < size) return RTF.format(Math.round(delta), unit);
    delta /= size;
  }
  return "";
}

export function localTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

// Eight colors derived from a 64-hex hash: a fingerprint you can compare at a
// glance (does this record's prev_hash match the one before it?). Fixed
// saturation/lightness keep every strip in the palette's range.
export function hashColors(hex, bands = 8) {
  if (!hex || hex.length < bands * 4) return [];
  const out = [];
  for (let i = 0; i < bands; i++) {
    const v = parseInt(hex.slice(i * 4, i * 4 + 4), 16);
    const hue = Math.round((v / 0xffff) * 360);
    const light = 52 + (v % 3) * 8;
    out.push(`hsl(${hue} 62% ${light}%)`);
  }
  return out;
}

export const GENESIS = "0".repeat(64);
