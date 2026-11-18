import { describe, expect, it } from "vitest";
import {
  ORBIT_TILT, alongRoute, buildScene, commPeer, createArrivalFlasher, ellipsePoint, flaringHubs, isElsewhere,
  layoutSystem, memberRows, moonsOf, pathRows, pulseRoute, sourceRows, touchList, travelMs,
} from "./topology.js";

const SINCE = "2026-11-13T00:00:00+00:00";
const T0 = Date.parse(SINCE);

// `/admin/profile/episodes` responses (#401 shape): pulses are [t, transit, type, category, source, summary].
const rover = {
  since: SINCE, until: "2026-11-14T00:00:00+00:00", count: 6,
  pulses: [
    [10, 0.4, "perception", "sense", "camera", "saw a face"],
    [20, 0.2, "perception", "sense", "mic", "heard: hello"],
    [30, 1.0, "perception", "self", "battery", "health"],
    [40, 2.0, "communication", "comm", "llm", "reply: hi"],
    [50, 0.1, "event", "act", "cliff", "cliff detected on"],
    [60, 0.3, "communication", "comm", "hub", "told rover-2"],
  ],
  lanes: { mind: [[35, 40, "thinking", "LLM request"]] },
  heartbeat: { interval_s: 30, last_t: 30 },
};
const hubEp = { since: SINCE, count: 1, pulses: [[5, 0.1, "system", null, "hub", "started"]], lanes: { mind: [] } };
const rover2 = { since: SINCE, count: 1, pulses: [[15, 0.1, "perception", "sense", "imu", "moved"]], lanes: {} };

const roster = [
  { name: "rover-2", role: "member", kind: "robot", last_seen_at: null },
  { name: "hub-1", role: "hub", kind: "relay", last_seen_at: "2026-11-13T01:00:00Z" },
  { name: "rover-1", role: "member", kind: "robot", last_seen_at: "2026-11-13T00:59:00Z" },
  { name: "agent-1", role: "observer", kind: "agent" },
];

function scene(extra = {}) {
  return buildScene({
    roster,
    episodes: { "rover-1": rover, "hub-1": hubEp, "rover-2": rover2 },
    models: { "rover-1": [{ name: "small-llm", runtime: "hub-1" }] },
    ...extra,
  });
}

describe("layout maths", () => {
  it("puts points on the ellipse", () => {
    const p = ellipsePoint(100, 50, 40, 40 * ORBIT_TILT, Math.PI / 3);
    expect(((p.x - 100) / 40) ** 2 + ((p.y - 50) / (40 * ORBIT_TILT)) ** 2).toBeCloseTo(1, 10);
  });

  it("orbits planets around their hub, sorted by name, each on its own ellipse", () => {
    const s = scene();
    expect(s.hubs).toEqual([{ name: "hub-1", planets: ["rover-1", "rover-2"] }]);
    expect(s.elsewhere).toEqual(["agent-1"]); // an AI agent with no shared address
    const L = layoutSystem(s, { cx: 300, cy: 200, size: 500, timeS: 12.5 });
    expect(L.stars["hub-1"]).toMatchObject({ x: 300, y: 200 });
    for (const name of ["rover-1", "rover-2", "agent-1"]) {
      const { x, y, orbit } = L.planets[name];
      expect(((x - orbit.cx) / orbit.rx) ** 2 + ((y - orbit.cy) / orbit.ry) ** 2).toBeCloseTo(1, 10);
      expect(orbit.ry).toBeCloseTo(orbit.rx * ORBIT_TILT, 10);
    }
    expect(L.planets["rover-1"].orbit.rx).toBeLessThan(L.planets["rover-2"].orbit.rx);
    expect(L.planets["agent-1"].orbit).toBe(L.outer);
  });

  it("freezes orbits when still", () => {
    const s = scene();
    const a = layoutSystem(s, { cx: 0, cy: 0, size: 400, timeS: 1, still: true });
    const b = layoutSystem(s, { cx: 0, cy: 0, size: 400, timeS: 99, still: true });
    expect(a.planets).toEqual(b.planets);
    expect(a.moons).toEqual(b.moons);
    const c = layoutSystem(s, { cx: 0, cy: 0, size: 400, timeS: 99 });
    expect(c.planets["rover-1"]).not.toEqual(a.planets["rover-1"]);
  });

  it("gives one moon per sensor source that produced records in range", () => {
    expect(moonsOf([{ source: "mic" }, { source: "camera" }, { source: "llm" }, { source: "mic" }])).toEqual(["camera", "mic"]);
    const s = scene();
    expect(s.planets["rover-1"].moons).toEqual(["camera", "mic", "cliff", "battery"]);
    expect(s.planets["rover-2"].moons).toEqual(["imu"]);
    expect(s.planets["agent-1"].moons).toEqual([]);
    const L = layoutSystem(s, { cx: 0, cy: 0, size: 400 });
    expect(Object.keys(L.moons).sort()).toEqual(
      ["rover-1:battery", "rover-1:camera", "rover-1:cliff", "rover-1:mic", "rover-2:imu"]);
  });

  it("keeps hubs a list", () => {
    const two = buildScene({ roster: [...roster, { name: "hub-0", role: "hub" }] });
    expect(two.hubs.map((h) => h.name)).toEqual(["hub-0", "hub-1"]);
    const L = layoutSystem(two, { cx: 0, cy: 0, size: 400 });
    expect(L.stars["hub-0"]).not.toEqual(L.stars["hub-1"]);
  });
});

describe("elsewhere", () => {
  const net = [
    { name: "hub-1", seen: { ip: "203.0.113.5" }, reported: [{ ip: "10.0.0.2" }] },
    { name: "rover-1", seen: { ip: "203.0.113.5" }, reported: [] },
    { name: "far", seen: { ip: "198.51.100.7" }, reported: [] },
    { name: "relayed", seen: { ip: "198.51.100.8" }, path: "via hub-1" },
  ];
  it("is no shared address with the hub", () => {
    expect(isElsewhere({ name: "rover-1", kind: "robot" }, "hub-1", net)).toBe(false);
    expect(isElsewhere({ name: "far", kind: "robot" }, "hub-1", net)).toBe(true);
    expect(isElsewhere({ name: "relayed", kind: "robot" }, "hub-1", net)).toBe(false);
    expect(isElsewhere({ name: "unknown", kind: "robot" }, "hub-1", net)).toBe(false);
    expect(isElsewhere({ name: "unknown", kind: "agent" }, "hub-1", net)).toBe(true);
    expect(isElsewhere({ name: "far", kind: "robot" }, null, net)).toBe(false);
  });
});

describe("pulses", () => {
  const s = scene();
  const route = (member, source, category = "sense") => pulseRoute({ member, source, category }, s);

  it("route per source", () => {
    expect(route("rover-1", "camera")).toEqual(["moon:rover-1:camera", "planet:rover-1", "star:hub-1", "ltms"]);
    expect(route("rover-1", "battery", "self")).toEqual(["moon:rover-1:battery", "planet:rover-1", "star:hub-1", "ltms"]);
    expect(route("rover-1", "llm", "comm")).toEqual(["star:hub-1", "planet:rover-1", "star:hub-1", "ltms"]);
    expect(route("rover-1", "stt", "act")).toEqual(["planet:rover-1", "star:hub-1", "ltms"]);
    expect(route("rover-1", "hub", "comm")).toEqual(["planet:rover-1", "star:hub-1", "ltms"]);
    expect(route("hub-1", "hub", null)).toEqual(["star:hub-1", "ltms"]);
    expect(route("agent-1", "hub", "act")).toEqual(["planet:agent-1", "ltms"]); // elsewhere: straight to FLTMS
  });

  it("travel time is 0.9 s + transit × 0.15 s, within the style guide's 2.5 s", () => {
    expect(travelMs(0)).toBe(900);
    expect(travelMs(4)).toBe(1500);
    expect(travelMs(null)).toBe(900);
    expect(travelMs(3600)).toBe(2500);
  });

  it("walks the route leg by leg", () => {
    const pts = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }];
    expect(alongRoute(pts, 0)).toMatchObject({ x: 0, y: 0, leg: 0 });
    expect(alongRoute(pts, 0.25, 0)).toMatchObject({ x: 5, y: 0 });
    expect(alongRoute(pts, 0.25, 4).y).toBeCloseTo(-4, 10); // lifted mid-leg
    expect(alongRoute(pts, 1)).toMatchObject({ x: 10 });
    expect(alongRoute(pts, 1).y).toBeCloseTo(10, 6);
  });

  it("arcs a message to the member's usual partner", () => {
    const edges = [{ a: "rover-1", b: "rover-2", category: "comm", count: 5 },
      { a: "rover-1", b: "agent-1", category: "comm", count: 2 },
      { a: "rover-1", b: "hub-1", category: "comm", count: 50 }]; // a hub is a star, not a planet
    expect(commPeer("rover-1", edges, s)).toBe("rover-2");
    expect(commPeer("rover-2", [], s)).toBe(null);
  });

  it("merges arrival flashes to at most 3 a second", () => {
    const f = createArrivalFlasher(3);
    for (let t = 0; t <= 2000; t += 20) f.arrive("#fff", t); // 50 arrivals a second
    for (let t = 0; t <= 2000; t += 10) f.level(t);
    for (let i = 1; i < f.flashes.length; i++) expect(f.flashes[i] - f.flashes[i - 1]).toBeGreaterThanOrEqual(1000 / 3);
    expect(f.flashes.length).toBeGreaterThanOrEqual(6);
    expect(f.flashes.length).toBeLessThanOrEqual(7);
    // A merged flash is brighter, and its color is the newest arrival's.
    const g = createArrivalFlasher(3);
    g.arrive("#aaa", 0);
    g.arrive("#bbb", 100);
    g.arrive("#ccc", 200);
    expect(g.level(400)).toMatchObject({ color: "#ccc" });
    expect(g.level(400).a).toBeGreaterThan(g.level(0 + 1)?.a ?? 0);
  });

  it("flares the hub while a model running on it is thinking", () => {
    expect(flaringHubs(s, T0 + 36_000).has("hub-1")).toBe(true);
    expect(flaringHubs(s, T0 + 41_000).size).toBe(0);
    const noModel = scene({ models: {} });
    expect(flaringHubs(noModel, T0 + 36_000).size).toBe(0);
  });
});

describe("touch", () => {
  it("names each light, with a planet's heartbeat", () => {
    const list = touchList(scene(), { bpm: (p) => (p.heartbeat?.interval_s ? 48 : null) });
    expect(list.map((x) => x.label)).toEqual([
      "FLTMS", "hub-1",
      "agent-1",
      "rover-1", "rover-1 (camera)", "rover-1 (mics)", "rover-1 (cliff sensors)", "rover-1 (battery)",
      "rover-2", "rover-2 (IMU)",
    ]);
    const byId = Object.fromEntries(list.map((x) => [x.id, x.narration]));
    expect(byId["planet:rover-1"]).toBe("rover-1, a member planet around hub-1, beating at 48 bpm.");
    expect(byId["planet:rover-2"]).toContain("no heartbeat in this range");
    expect(byId["planet:agent-1"]).toContain("elsewhere");
    expect(byId["star:hub-1"]).toBe("hub-1, the star: relays for 2 members. A model runs here.");
    expect(byId.ltms).toContain("Every record ends here");
    expect(touchList(scene(), { skipLtms: true })[0].id).toBe("star:hub-1");
    expect(new Set(list.map((x) => x.id)).size).toBe(list.length);
  });

  it("has unique labels with no role suffix", () => {
    const list = touchList(scene(), { bpm: (p) => (p.heartbeat?.interval_s ? 48 : null) });
    const labels = list.map((x) => x.label);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels.some((l) => /\(star\)|\(planet\)|\(moon\)/.test(l))).toBe(false);
  });
});

describe("table twin", () => {
  it("counts match /profile/episodes", () => {
    const s = scene();
    const total = rover.count + hubEp.count + rover2.count;
    expect(sourceRows(s).reduce((n, r) => n + r.records, 0)).toBe(total);
    expect(pathRows(s).reduce((n, r) => n + r.records, 0)).toBe(total);
    const r1 = sourceRows(s).filter((r) => r.member === "rover-1");
    expect(r1.map((r) => [r.source, r.records])).toEqual(
      [["camera", 1], ["mic", 1], ["cliff", 1], ["battery", 1], ["hub", 1], ["llm", 1]]);
    // Up to the hand only.
    expect(pathRows(s, T0 + 20_000).reduce((n, r) => n + r.records, 0)).toBe(4);
    const camera = pathRows(s).find((r) => r.from === "rover-1 camera");
    expect(camera).toMatchObject({ through: "rover-1 → hub-1", to: "FLTMS", records: 1 });
    expect(pathRows(s).find((r) => r.from === "hub-1" && r.through === "rover-1 → hub-1")).toBeDefined(); // LLM reply
    expect(pathRows(s).find((r) => r.from === "hub-1" && r.through === "direct")).toBeDefined();
  });

  it("lists members with role, kind, home hub and last seen", () => {
    const rows = memberRows(scene(), roster);
    expect(rows.map((r) => [r.name, r.role, r.hub])).toEqual([
      ["agent-1", "observer", "elsewhere"], ["hub-1", "hub", "is the hub"],
      ["rover-1", "member", "hub-1"], ["rover-2", "member", "hub-1"]]);
    expect(rows[2].lastSeen).toBe("2026-11-13T00:59:00Z");
  });
});
