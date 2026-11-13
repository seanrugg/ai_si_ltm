// Sites for the Network tab (issues #195, #196): where members connect from.
import { CATEGORIES } from "./visual.js";

const EARTH_KM = 6371;

/** Records per minute per category from a summary over the last hour. */
export function ratesFrom(summary) {
  const out = {};
  for (const c of CATEGORIES) out[c.key] = (summary?.categories?.[c.key] || 0) / 60;
  return out;
}

/** Halo radius in px for a location uncertainty, on a globe of radius R px. */
export function haloPx(accuracyKm, R) {
  return Math.max(4, (accuracyKm / EARTH_KM) * R);
}

function noteFor(best, member) {
  if (!best) return null;
  if (best.source === "declared") return best.inherited_from ? `declared for ${best.inherited_from}` : "declared";
  if (best.source === "gps") return "GPS";
  if (best.source === "network") {
    if (member?.kind === "agent" || member?.kind === "assistant") return "data-center estimate";
    const via = best.via_hub ? `, via hub ${best.via_hub}` : best.inherited_from ? `, from ${best.inherited_from}` : "";
    return `estimate ±${Math.round(best.accuracy_km ?? 50)} km${via}`;
  }
  return best.source;
}

/**
 * Members at the same declared location form one site (the swarm's home);
 * every other member with a located best position is its own site.
 * Members without coordinates get no site (they still appear in the list).
 * Returns [{ key, label, lon, lat, source, note, accuracy_km, members: [names], home }].
 */
export function sitesOf(locations, members = []) {
  const byName = Object.fromEntries(members.map((m) => [m.name, m]));
  const groups = new Map();
  const solo = [];
  for (const loc of locations || []) {
    const b = loc.best;
    if (!b || b.lat == null || b.lon == null) continue;
    if (b.source === "declared") {
      const key = `${b.label}|${b.lat}|${b.lon}`;
      if (!groups.has(key)) {
        groups.set(key, { key, label: b.label, lon: b.lon, lat: b.lat, source: "declared", note: "declared",
          accuracy_km: null, members: [], home: false });
      }
      groups.get(key).members.push(loc.name);
    } else {
      solo.push({ key: `member:${loc.name}`, label: loc.name, lon: b.lon, lat: b.lat, source: b.source,
        note: noteFor(b, byName[loc.name]), accuracy_km: b.accuracy_km ?? null, members: [loc.name], home: false });
    }
  }
  const declared = [...groups.values()].sort((a, b) => b.members.length - a.members.length || a.label.localeCompare(b.label));
  if (declared.length) declared[0].home = true; // the biggest declared site is the swarm's home
  return [...declared, ...solo];
}

/** Rooms for the Home view: [{ room, members: [names] }], "Unassigned" last. */
export function roomsOf(locations, names) {
  const want = new Set(names);
  const rooms = new Map();
  const unassigned = [];
  for (const loc of locations || []) {
    if (!want.has(loc.name)) continue;
    const declared = (loc.all || []).find((e) => e.source === "declared");
    const room = declared?.room || null;
    if (room) {
      if (!rooms.has(room)) rooms.set(room, []);
      rooms.get(room).push(loc.name);
    } else unassigned.push(loc.name);
  }
  const out = [...rooms.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([room, m]) => ({ room, members: m.sort() }));
  if (unassigned.length) out.push({ room: "Unassigned", members: unassigned.sort() });
  return out;
}

/** Line width for a flow: 1.2 px at the lowest rate up to 5 px at `max`. */
export function flowWidth(perMinute, max) {
  if (!(perMinute > 0) || !(max > 0)) return 1.2;
  return 1.2 + Math.min(1, perMinute / max) * (5 - 1.2);
}

/** Members with no position, each with the server's reason (#346). */
export function unplacedOf(locations) {
  return (locations || []).filter((l) => !l.best || l.best.lat == null || l.best.lon == null)
    .map((l) => ({ name: l.name, reason: l.unplaced || "No location yet." }));
}
