// Geography (#405, docs/holo-light.md "Geography"): pure data shaping for the
// globe and its table twin. Where swarms are, which members float as
// satellites, who is unplaced, and the words for each light.
//
// Scope-free on purpose: the profile tab passes one swarm (or one member), and
// the fleet-wide Geography page (#382) passes everything.
//
// Addresses are admin-only (#189): nothing built here carries an IP address,
// and `redactAddresses` strips any from server text (unplaced reasons) before
// it is narrated or put on the canvas.
import { EARTH_KM } from "./globe.js";
import { kindOf } from "./visual.js";

const DEFAULT_ACCURACY_KM = 50;

const IPV4 = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
// IPv6: "::" somewhere, or at least four colon-separated hex groups (so a time like 9:02 stays).
const IPV6 = /\b(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}\b/gi;

function isV6(s) {
  return s.includes("::") || (s.match(/:/g) || []).length >= 4;
}

/** Text with every IP address taken out: "(10.0.0.5)" goes, a bare address becomes "an address". */
export function redactAddresses(text) {
  if (text == null) return text;
  let out = String(text)
    .replace(/\s*\(\s*(?:\d{1,3}\.){3}\d{1,3}\s*\)/g, "")
    .replace(/\s*\(\s*([0-9a-f:]+)\s*\)/gi, (m, ip) => (isV6(ip) ? "" : m))
    .replace(IPV4, "an address");
  out = out.replace(IPV6, (m) => (isV6(m) ? "an address" : m));
  return out;
}

/**
 * A cloud agent floats as a satellite: an AI agent (software only) whose place
 * would be its provider's. One with a declared place or GPS is drawn there.
 */
export function isSatellite(member, best) {
  if (kindOf(member) !== "agent") return false;
  const placed = best && best.lat != null && best.lon != null && (best.source === "declared" || best.source === "gps");
  return !placed;
}

function placeKey(best) {
  return `${Number(best.lat).toFixed(3)},${Number(best.lon).toFixed(3)}`;
}

const SOURCE_RANK = { declared: 0, gps: 1, network: 2 };

/**
 * Build what the globe draws.
 *   locations  `/admin/locations` response ({members: [{name, best, unplaced}]})
 *   swarms     `/admin/swarms` list ([{name, members: [{name, role}]}])
 *   members    `/admin/members` rows ({name, kind, cognition, ...})
 *   counts     {member: records in range}
 * Returns {
 *   lights:     [{id, swarm, label, place, lon, lat, source, estimated, accuracyKm, members, hubs, records}],
 *               one per swarm per place (members at one address are one light), and one per
 *               free agent; a swarm's home light comes first
 *   links:      [[lightId, lightId]] joining the lights of a swarm split across sites (#398)
 *   satellites: [{id, name, swarm, records}]
 *   unplaced:   [{id, name, swarm, reason}] (reason without addresses)
 * }
 */
export function buildGeography({ locations, swarms = [], members = [], counts = {} } = {}) {
  const rows = locations?.members || [];
  const byName = Object.fromEntries((members || []).map((m) => [m.name, m]));
  const membership = {};
  for (const s of swarms || []) for (const m of s.members || []) membership[m.name] = { swarm: s.name, role: m.role };

  const groups = new Map();
  const satellites = [];
  const unplaced = [];
  for (const row of rows) {
    const name = row.name;
    const swarm = membership[name]?.swarm ?? null;
    const role = membership[name]?.role ?? null;
    const member = { ...byName[name], name, role };
    const best = row.best;
    const records = counts[name] || 0;
    if (isSatellite(member, best)) {
      satellites.push({ id: `sat:${name}`, name, swarm, records });
      continue;
    }
    if (!best || best.lat == null || best.lon == null) {
      unplaced.push({ id: `unplaced:${name}`, name, swarm, reason: redactAddresses(row.unplaced || "No location yet.") });
      continue;
    }
    const key = `${swarm ? `swarm:${swarm}` : `free:${name}`}@${placeKey(best)}`;
    if (!groups.has(key)) {
      groups.set(key, {
        id: key, swarm, label: swarm ?? name, place: best.label || null, lon: Number(best.lon), lat: Number(best.lat),
        source: best.source, estimated: best.source === "network", accuracyKm: null, members: [], hubs: [], records: 0,
      });
    }
    const g = groups.get(key);
    g.members.push(name);
    if (role === "hub") g.hubs.push(name);
    g.records += records;
    if (SOURCE_RANK[best.source] < SOURCE_RANK[g.source]) {
      g.source = best.source;
      g.place = best.label || g.place;
    }
    g.estimated = g.source === "network";
    if (best.source === "network") g.accuracyKm = Math.max(g.accuracyKm ?? 0, best.accuracy_km ?? DEFAULT_ACCURACY_KM);
    if (!g.place && best.label) g.place = best.label;
  }
  for (const g of groups.values()) if (!g.estimated) g.accuracyKm = null;

  // Home first: the light with a hub (declared, or the primary hub's estimate), then the biggest.
  // Swarms first (by name), then free agents.
  const lights = [...groups.values()].sort((a, b) => (a.swarm ? 0 : 1) - (b.swarm ? 0 : 1)
    || (a.swarm ?? a.label).localeCompare(b.swarm ?? b.label)
    || b.hubs.length - a.hubs.length || b.members.length - a.members.length || a.id.localeCompare(b.id));
  const links = [];
  const bySwarm = new Map();
  for (const l of lights) {
    if (!l.swarm) continue;
    if (!bySwarm.has(l.swarm)) bySwarm.set(l.swarm, []);
    bySwarm.get(l.swarm).push(l);
  }
  for (const list of bySwarm.values()) for (let i = 1; i < list.length; i++) links.push([list[0].id, list[i].id]);
  satellites.sort((a, b) => a.name.localeCompare(b.name));
  unplaced.sort((a, b) => a.name.localeCompare(b.name));
  return { lights, links, satellites, unplaced };
}

/** The light a member is in (its place), or null. */
export function lightOf(geo, name) {
  return geo.lights.find((l) => l.members.includes(name)) ?? null;
}

/**
 * How a light's place is drawn: a network estimate is a dashed disc of its
 * accuracy radius, never a point; a declared or GPS place is a point.
 */
export function markOf(light) {
  if (light?.estimated) return { shape: "disc", radiusKm: light.accuracyKm ?? DEFAULT_ACCURACY_KM, dashed: true };
  return { shape: "point" };
}

/** A disc's radius in px for `km` on a globe of radius R px (at least 6 px, so it reads as a disc). */
export function discPx(km, R) {
  return Math.max(6, (km / EARTH_KM) * R);
}

/** A light's radius in px from the log of its records in range. */
export function lightRadius(records) {
  const n = Math.max(0, Number(records) || 0);
  return Math.min(30, 9 + 5 * Math.log10(1 + n));
}

/** "declared", "GPS" or "network, estimated (±50 km)". */
export function sourceText(light) {
  if (light.source === "gps") return "GPS";
  if (light.source === "network") return `network, estimated (±${Math.round(light.accuracyKm ?? DEFAULT_ACCURACY_KM)} km)`;
  return "declared";
}

function count(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

/** What touching a light says. Estimates say "estimated". No addresses. */
export function lightNarration(light) {
  const who = light.swarm
    ? `${light.label}: ${count(light.members.length, "member")} here${light.hubs.length ? `, hub ${light.hubs.join(", ")}` : ""}`
    : `${light.label}, a free agent`;
  const where = light.estimated
    ? `Estimated place${light.place ? `: ${light.place}` : ""}, within about ${Math.round(light.accuracyKm ?? DEFAULT_ACCURACY_KM)} km`
    : `${light.source === "gps" ? "GPS" : "Declared"} place${light.place ? `: ${light.place}` : ""}`;
  return `${who}. ${where}. ${count(light.records, "record")} in range.`;
}

export function satelliteNarration(s) {
  return `${s.name}, a cloud agent: a satellite. Its place is its provider's.`;
}

export function unplacedNarration(u) {
  return `${u.name} is unplaced: ${u.reason}`;
}

export function ltmsNarration(server) {
  return server
    ? `LTMS, the pyramid${server.label ? ` at ${server.label}` : ""}. Every record ends here.`
    : "LTMS, the pyramid. Its place isn't declared, so it waits at the frame edge. Every record ends here.";
}

/** Records per member up to the hand `t` (ms), from sorted events [{t, member}]. */
export function countsUpTo(events, t = Infinity) {
  const out = {};
  for (const e of events || []) {
    if (e.t > t) break;
    out[e.member] = (out[e.member] || 0) + 1;
  }
  return out;
}

/**
 * The twin's Places table: one row per light, one for the satellites, and LTMS.
 * `atHand`: {member: records up to the hand}.
 */
export function placeRows(geo, server, atHand = {}) {
  const sum = (names) => names.reduce((s, n) => s + (atHand[n] || 0), 0);
  const rows = geo.lights.map((l) => ({
    id: l.id,
    site: l.place ? `${l.label} · ${l.place}` : l.label,
    members: l.members.join(", "),
    source: sourceText(l),
    estimated: l.estimated,
    records: sum(l.members),
  }));
  if (geo.satellites.length) {
    const names = geo.satellites.map((s) => s.name);
    rows.push({ id: "satellites", site: "Cloud (satellites)", members: names.join(", "),
      source: "provider's place", estimated: false, records: sum(names) });
  }
  rows.push({ id: "ltms", site: "LTMS", members: "—", source: server ? "declared" : "not declared",
    estimated: false, records: null });
  return rows;
}

/**
 * The touchable lights on the globe, in reading order: LTMS, each light, each
 * satellite, each unplaced member. Each is {id, label, narration, note}.
 */
export function globeTouchables(geo, server) {
  const out = [{ id: "ltms", label: "LTMS", narration: ltmsNarration(server), note: 7 }];
  geo.lights.forEach((l, i) => {
    out.push({ id: l.id, label: `${l.label}${l.place ? ` · ${l.place}` : ""}${l.estimated ? " (estimated)" : ""}`,
      narration: lightNarration(l), note: i % 5 });
  });
  for (const s of geo.satellites) out.push({ id: s.id, label: `${s.name} (satellite)`, narration: satelliteNarration(s), note: 9 });
  for (const u of geo.unplaced) out.push({ id: u.id, label: `${u.name} (unplaced)`, narration: unplacedNarration(u), note: 3 });
  return out;
}
