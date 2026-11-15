// Topology, the star system (#404, docs/holo-light.md "Topology"): pure maths
// and data shaping, shared by SystemView (the drawing) and the Topology tab's
// table twin. Geography (#405) reuses the same scene and layout.
//
// Scene: hubs are stars, members are planets on elliptical orbits around their
// home hub, a planet's senses are moons, members with no shared address with
// the hub sit on a dashed outer orbit ("elsewhere"), and LTMS is the pyramid.
//
// Hubs are a list everywhere. Today's swarms have one hub; multi-hub layouts
// (#398) only need a richer `homeHub` and hub placement.
import { CATEGORIES, CAT_SELF, RELAY, ROLES, roleOf } from "./visual.js";
import { createFlashGate } from "./light.js";

// The sensors that become moons, in moon order, with their names in plain words.
export const MOON_SOURCES = ["camera", "mic", "imu", "cliff", "battery"];
export const SOURCE_LABELS = {
  camera: "camera", mic: "mics", imu: "IMU", cliff: "cliff sensors", battery: "battery",
  stt: "speech to text", llm: "LLM reply", hub: "other records",
};

// Orbit motion, radians per second: slow, because nothing spins for show.
export const PLANET_SPIN = 0.06;
export const MOON_SPIN = 0.4;
// Ellipses are seen from above at an angle: height = width × this.
export const ORBIT_TILT = 0.38;
const MOON_TILT = 0.6;

// Travel: 0.9 s + measured transit × 0.15 s (#404), held to the style guide's
// 0.9–2.5 s so a record buffered for hours doesn't crawl for minutes.
export const TRAVEL = { baseMs: 900, perTransitSecondMs: 150, maxMs: 2500 };

export function travelMs(transitS) {
  const transit = Number.isFinite(transitS) && transitS > 0 ? transitS : 0;
  return Math.min(TRAVEL.maxMs, TRAVEL.baseMs + transit * TRAVEL.perTransitSecondMs);
}

/** The point at `angle` on the ellipse centred on (cx, cy) with radii rx, ry. */
export function ellipsePoint(cx, cy, rx, ry, angle) {
  return { x: cx + Math.cos(angle) * rx, y: cy + Math.sin(angle) * ry };
}

/** A category's light color; self-reports are cat-self; `system` and unknown are relay gray. */
export function categoryColor(key) {
  if (key === "self") return CAT_SELF;
  return CATEGORIES.find((c) => c.key === key)?.color ?? RELAY;
}

// ---------------------------------------------------------------- scene

function ipsOf(entry) {
  if (!entry) return new Set();
  const ips = [entry.seen?.ip, ...(entry.reported || []).map((r) => r.ip)].filter(Boolean);
  return new Set(ips);
}

/**
 * True when a member is "elsewhere": it shares no address with its hub
 * (`/admin/network` entries; seen or reported). A member whose newest record
 * came "via" the hub is never elsewhere. With no address data, only AI agents
 * (software, no body) count as elsewhere, because their place is their provider's.
 */
export function isElsewhere(member, hub, network) {
  if (!hub || member.name === hub) return false;
  const byName = Object.fromEntries((network || []).map((n) => [n.name, n]));
  const mine = byName[member.name];
  if (mine?.path === `via ${hub}`) return false;
  const a = ipsOf(mine);
  const b = ipsOf(byName[hub]);
  if (a.size && b.size) return ![...a].some((ip) => b.has(ip));
  return member.kind === "agent" || member.kind === "assistant";
}

/** The moon sources a member's pulses show, in moon order: one per source with records in range. */
export function moonsOf(pulses) {
  const seen = new Set((pulses || []).map((p) => p.source));
  return MOON_SOURCES.filter((s) => seen.has(s));
}

/**
 * Turn `/profile/episodes` pulses into events: {t (epoch ms), member, transit,
 * type, category, source, summary}. `since` is the response's ISO start.
 */
export function eventsOf(member, episodes) {
  const t0 = Date.parse(episodes?.since);
  if (!Number.isFinite(t0)) return [];
  return (episodes.pulses || []).map(([t, transit, type, cat, source, summary]) => ({
    t: t0 + t * 1000, member, transit, type, category: cat, source, summary,
  }));
}

/** Mind-lane "thinking" episodes as [startMs, endMs]. */
export function thinkingOf(episodes) {
  const t0 = Date.parse(episodes?.since);
  if (!Number.isFinite(t0)) return [];
  return (episodes?.lanes?.mind || []).filter((e) => e[2] === "thinking")
    .map(([a, b]) => [t0 + a * 1000, t0 + b * 1000]);
}

/** True when a model of this member runs on `hub` (specs `models[].runtime`, from cognition's runs_on). */
export function runsOnHub(models, hub) {
  return Boolean(hub) && (models || []).some((m) => m && (m.runtime === hub || m.runs_on === hub));
}

/**
 * Build the scene.
 *   roster    [{name, role, kind, cognition, last_seen_at, homeHub?}]
 *   network   `/admin/network` members (optional)
 *   episodes  {name: `/profile/episodes` response} for members whose records are shown
 *   models    {name: [{name, runtime}]} from specs (optional)
 *   focus     a member name: the member profile centres it and dims the rest
 * Returns {hubs: [{name, planets: [names]}], planets: {name: planet}, order: [names],
 *   events (sorted), thinking: [{member, hub, spans}], focus}.
 */
export function buildScene({ roster = [], network = [], episodes = {}, models = {}, focus = null } = {}) {
  const byName = [...roster].sort((a, b) => a.name.localeCompare(b.name));
  const hubNames = byName.filter((m) => roleOf(m) === "hub").map((m) => m.name);
  const hubs = hubNames.map((name) => ({ name, planets: [] }));
  const homeOf = (m) => (hubNames.includes(m.homeHub) ? m.homeHub : hubNames[0] ?? null);

  const planets = {};
  const events = [];
  const thinking = [];
  for (const m of byName) {
    const ep = episodes[m.name];
    const evs = ep ? eventsOf(m.name, ep) : [];
    events.push(...evs);
    if (hubNames.includes(m.name)) {
      if (ep) thinking.push({ member: m.name, hub: m.name, spans: thinkingOf(ep) });
      continue;
    }
    const home = homeOf(m);
    const elsewhere = isElsewhere(m, home, network);
    planets[m.name] = {
      name: m.name,
      role: roleOf(m),
      kind: m.kind ?? null,
      cognition: m.cognition ?? null,
      lastSeen: m.last_seen_at ?? null,
      hub: home,
      elsewhere,
      moons: moonsOf(evs),
      heartbeat: ep?.heartbeat ?? null,
      shown: Boolean(ep),
    };
    if (!elsewhere && home) hubs.find((h) => h.name === home).planets.push(m.name);
    if (ep && runsOnHub(models[m.name], home)) thinking.push({ member: m.name, hub: home, spans: thinkingOf(ep) });
  }
  events.sort((a, b) => a.t - b.t);
  const order = Object.keys(planets);
  return {
    hubs,
    planets,
    order,
    orbiting: order.filter((n) => !planets[n].elsewhere && planets[n].hub),
    unhomed: order.filter((n) => !planets[n].elsewhere && !planets[n].hub),
    elsewhere: order.filter((n) => planets[n].elsewhere),
    events,
    thinking,
    focus: focus && (planets[focus] || hubNames.includes(focus)) ? focus : null,
  };
}

/** Hubs whose star flares with `mind` at time t (ms): a model on that hub is thinking. */
export function flaringHubs(scene, t) {
  const out = new Set();
  for (const { hub, spans } of scene.thinking) {
    if (spans.some(([a, b]) => a <= t && t < b)) out.add(hub);
  }
  return out;
}

// ---------------------------------------------------------------- layout

/**
 * Positions for one frame. `cx, cy` the system's centre, `size` its width,
 * `timeS` wall seconds (orbits drift with it), `still` freezes the orbits
 * (Calm view, reduced motion). Returns
 *   {stars: {name: {x, y, r}}, planets: {name: {x, y, r, orbit}}, moons:
 *    {"member:source": {x, y, r}}, outer: {cx, cy, rx, ry}, centre: {x, y}}
 * where orbit = {cx, cy, rx, ry}. Inner orbits are evenly spaced by name order:
 * distance carries no meaning.
 */
export function layoutSystem(scene, { cx, cy, size, timeS = 0, still = false }) {
  const spin = still ? 0 : timeS;
  const stars = {};
  const n = scene.hubs.length;
  const spread = n > 1 ? size * 0.2 : 0;
  scene.hubs.forEach((h, i) => {
    const p = ellipsePoint(cx, cy, spread, spread * ORBIT_TILT, -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, n));
    stars[h.name] = { x: p.x, y: p.y, r: size * 0.07 };
  });
  const planets = {};
  const place = (names, centreOf, inner, outer) => {
    names.forEach((name, i) => {
      const c = centreOf(name);
      const rx = names.length > 1 ? inner + ((outer - inner) * i) / (names.length - 1) : (inner + outer) / 2;
      const orbit = { cx: c.x, cy: c.y, rx, ry: rx * ORBIT_TILT };
      const angle = 0.3 + (2 * Math.PI * i) / names.length + spin * PLANET_SPIN;
      planets[name] = { ...ellipsePoint(orbit.cx, orbit.cy, orbit.rx, orbit.ry, angle), r: size * 0.035, orbit };
    });
  };
  const hubScale = n > 1 ? 0.5 : 1;
  for (const h of scene.hubs) {
    place(h.planets, () => stars[h.name], size * 0.18 * hubScale, size * 0.34 * hubScale);
  }
  place(scene.unhomed || [], () => ({ x: cx, y: cy }), size * 0.18, size * 0.34);
  const outer = { cx, cy, rx: size * 0.46, ry: size * 0.46 * ORBIT_TILT };
  (scene.elsewhere || []).forEach((name, i, all) => {
    const angle = 4 + (2 * Math.PI * i) / all.length + spin * PLANET_SPIN * 0.5;
    planets[name] = { ...ellipsePoint(outer.cx, outer.cy, outer.rx, outer.ry, angle), r: size * 0.028, orbit: outer };
  });
  const moons = {};
  for (const name of scene.order) {
    const p = planets[name];
    const list = scene.planets[name].moons;
    list.forEach((src, j) => {
      const a = (2 * Math.PI * j) / list.length + spin * MOON_SPIN;
      const rr = size * 0.065;
      moons[`${name}:${src}`] = { ...ellipsePoint(p.x, p.y, rr, rr * MOON_TILT, a), r: Math.max(3, size * 0.008) };
    });
  }
  return { stars, planets, moons, outer, centre: { x: cx, y: cy } };
}

// ---------------------------------------------------------------- pulses

/**
 * The nodes a record travels through, as keys: "moon:<member>:<source>",
 * "planet:<member>", "star:<hub>", "ltms".
 *   a sensed record:  moon → planet → star → pyramid
 *   an LLM reply:     star → planet → star → pyramid
 *   a hub's own:      star → pyramid
 *   elsewhere / no hub: (moon →) planet → pyramid
 */
export function pulseRoute(event, scene) {
  const m = event.member;
  if (scene.hubs.some((h) => h.name === m)) return [`star:${m}`, "ltms"];
  const planet = scene.planets[m];
  if (!planet) return ["ltms"];
  const hub = planet.elsewhere ? null : planet.hub;
  const moon = planet.moons.includes(event.source) ? [`moon:${m}:${event.source}`] : [];
  if (event.source === "llm" && hub) return [`star:${hub}`, `planet:${m}`, `star:${hub}`, "ltms"];
  return [...moon, `planet:${m}`, ...(hub ? [`star:${hub}`] : []), "ltms"];
}

/**
 * The peer a member's message arcs to: the roster member it exchanges the most
 * Communicating records with (from `/profile/graph` edges), or null. Pulses
 * don't name a message's recipient, so the arc goes to the usual partner.
 */
export function commPeer(member, edges, scene) {
  let best = null;
  for (const e of edges || []) {
    if (e.category !== "comm" || (e.a !== member && e.b !== member)) continue;
    const other = e.a === member ? e.b : e.a;
    if (!scene.planets[other] || other === member) continue;
    if (!best || e.count > best.count) best = { name: other, count: e.count };
  }
  return best?.name ?? null;
}

/** A message between members (Communicating, not an LLM reply) whose arc can be drawn. */
export function isMemberMessage(event) {
  return event.category === "comm" && event.source !== "llm";
}

/** The position of a route key in a layout ({x, y}), or null. */
export function nodePos(key, layout, ltms) {
  if (key === "ltms") return ltms;
  const [kind, ...rest] = key.split(":");
  if (kind === "star") return layout.stars[rest.join(":")] ?? null;
  if (kind === "planet") return layout.planets[rest.join(":")] ?? null;
  if (kind === "moon") return layout.moons[rest.join(":")] ?? null;
  return null;
}

/**
 * Where a travelling light is: `u` 0..1 of the trip along `points` (equal time
 * per leg). Legs before the last lift in a small arc (`lift` px).
 */
export function alongRoute(points, u, lift = 0) {
  const legs = points.length - 1;
  if (legs < 1) return points[0] ?? null;
  const s = Math.min(legs - 1e-9, Math.max(0, u) * legs);
  const k = Math.floor(s);
  const v = s - k;
  const a = points[k];
  const b = points[k + 1];
  const arc = k < legs - 1 ? Math.sin(Math.PI * v) * lift : 0;
  return { x: a.x + (b.x - a.x) * v, y: a.y + (b.y - a.y) * v - arc, leg: k };
}

/**
 * Arrival flashes on one light, merged so it flashes at most `maxPerSecond`
 * times a second (WCAG 2.3.1): an arrival inside the gap joins the next flash,
 * which is a little brighter for it.
 *   arrive(color, nowMs)  → true when this arrival started a flash now
 *   level(nowMs)          → {color, a} (a: 0..1, 160 ms up then 0.7 s decay), or null
 *   flashes               the start times of every flash so far (for tests)
 */
export function createArrivalFlasher(maxPerSecond = 3, { holdMs = 160, decayMs = 700 } = {}) {
  const gate = createFlashGate(maxPerSecond);
  const gap = 1000 / maxPerSecond;
  let current = null; // {color, at, a}
  let pending = null; // {color, n, since}
  const flashes = [];
  function start(color, at, n) {
    current = { color, at, a: Math.min(1, 0.6 + 0.1 * n) };
    flashes.push(at);
  }
  return {
    arrive(color, nowMs) {
      if (pending) {
        pending.color = color;
        pending.n += 1;
      } else {
        pending = { color, n: 1 };
      }
      return this.flush(nowMs);
    },
    flush(nowMs) {
      if (!pending) return false;
      if (current && nowMs - current.at < gap) return false;
      if (!gate.allow("light", nowMs)) return false;
      start(pending.color, nowMs, pending.n);
      pending = null;
      return true;
    },
    level(nowMs) {
      this.flush(nowMs);
      if (!current) return null;
      const dt = nowMs - current.at;
      if (dt < 0 || dt > holdMs + decayMs) return null;
      const a = dt <= holdMs ? current.a : current.a * (1 - (dt - holdMs) / decayMs);
      return { color: current.color, a };
    },
    reset() {
      current = null;
      pending = null;
      gate.reset();
    },
    flashes,
  };
}

// ---------------------------------------------------------------- touch and sound

const PENTA = [0, 2, 4, 7, 9];

/** The touch note for a pentatonic degree (D major pentatonic from D4). */
export function noteHz(degree) {
  const d = Math.max(0, Math.round(degree));
  const semis = PENTA[d % 5] + 12 * Math.floor(d / 5);
  return 293.66 * 2 ** (semis / 12);
}

export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * The touchable lights, in reading order: the pyramid, each star, each planet,
 * each moon. Each is {id, label, narration, degree} for VizFrame's touch list.
 * `bpm(planet)` gives a planet's heartbeat rate, or null when it is quiet.
 */
export function touchList(scene, { bpm = () => null, skipLtms = false } = {}) {
  const out = [];
  if (!skipLtms) out.push({ id: "ltms", label: "LTMS", narration: "LTMS, the pyramid. Every record ends here.", degree: 7 });
  for (const h of scene.hubs) {
    const flare = scene.thinking.some((x) => x.hub === h.name) ? " A model runs here." : "";
    out.push({ id: `star:${h.name}`, label: `${h.name} (star)`,
               narration: `${h.name}, the star: relays for ${plural(h.planets.length, "member")}.${flare}`, degree: 0 });
  }
  scene.order.forEach((name, i) => {
    const p = scene.planets[name];
    const rate = bpm(p);
    const where = p.elsewhere ? " elsewhere" : p.hub ? ` around ${p.hub}` : "";
    const beat = rate ? `beating at ${Math.round(rate)} bpm` : "no heartbeat in this range";
    out.push({ id: `planet:${name}`, label: `${name} (planet)`,
               narration: `${name}, a ${ROLES[p.role]?.label.toLowerCase() ?? "member"} planet${where}, ${beat}.`,
               degree: 2 + i });
    p.moons.forEach((src, j) => {
      out.push({ id: `moon:${name}:${src}`, label: `${name} ${SOURCE_LABELS[src] ?? src} (moon)`,
                 narration: `${name}'s ${SOURCE_LABELS[src] ?? src}, a moon.`, degree: 5 + j });
    });
  });
  return out;
}

// ---------------------------------------------------------------- table twin

function nodeName(key) {
  if (key === "ltms") return "LTMS";
  const [kind, member, source] = key.split(":");
  if (kind === "moon") return `${member} ${SOURCE_LABELS[source] ?? source}`;
  return member;
}

/** Paths: one row per route, {id, from, through, to, records} counting events at or before `t`. */
export function pathRows(scene, t = Infinity) {
  const rows = new Map();
  for (const e of scene.events) {
    if (e.t > t) break;
    const route = pulseRoute(e, scene);
    const id = route.join(">");
    if (!rows.has(id)) {
      rows.set(id, {
        id,
        from: nodeName(route[0]),
        through: route.slice(1, -1).map(nodeName).join(" → ") || "direct",
        to: nodeName(route[route.length - 1]),
        records: 0,
      });
    }
    rows.get(id).records += 1;
  }
  return [...rows.values()].sort((a, b) => b.records - a.records || a.id.localeCompare(b.id));
}

/** By source: records in range per member and source (moons first, in moon order). */
export function sourceRows(scene) {
  const counts = new Map();
  for (const e of scene.events) {
    const id = `${e.member}:${e.source}`;
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  const rank = (s) => (MOON_SOURCES.includes(s) ? MOON_SOURCES.indexOf(s) : MOON_SOURCES.length);
  return [...counts.entries()].map(([id, records]) => {
    const i = id.lastIndexOf(":");
    const member = id.slice(0, i);
    const source = id.slice(i + 1);
    return { id, member, source, label: SOURCE_LABELS[source] ?? source, moon: MOON_SOURCES.includes(source), records };
  }).sort((a, b) => a.member.localeCompare(b.member) || rank(a.source) - rank(b.source) || a.source.localeCompare(b.source));
}

/** Members: {name, role, kind, hub, lastSeen} for every star and planet, by name. */
export function memberRows(scene, roster) {
  const hubs = new Set(scene.hubs.map((h) => h.name));
  return [...roster].sort((a, b) => a.name.localeCompare(b.name)).map((m) => ({
    id: m.name,
    name: m.name,
    role: roleOf(m),
    kind: m.kind ?? null,
    hub: hubs.has(m.name) ? "is the hub" : scene.planets[m.name]?.elsewhere
      ? "elsewhere" : (scene.planets[m.name]?.hub ?? "none"),
    lastSeen: m.last_seen_at ?? null,
  }));
}
