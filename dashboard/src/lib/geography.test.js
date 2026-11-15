import { describe, expect, it } from "vitest";
import {
  EASE_MS, IDLE_MS, MAX_ZOOM, MIN_ZOOM, arrowFor, clampZoom, createGlobeCamera, globeRadius, landDetail, limbRing,
  pinToEdge, project, projectAlt, ringCaps, systemFade, visibleCaps,
} from "./globe.js";
import {
  buildGeography, countsUpTo, discPx, globeTouchables, isSatellite, lightNarration, lightRadius, markOf, placeRows,
  redactAddresses,
} from "./geography.js";
import { smoothstep } from "./light.js";

const SWARMS = [{ name: "lab", members: [{ name: "hub-1", role: "hub" }, { name: "rover-1", role: "member" },
  { name: "helper", role: "member" }, { name: "far-hub", role: "hub" }] }];
const MEMBERS = [
  { name: "hub-1", kind: "relay" }, { name: "rover-1", kind: "robot" }, { name: "helper", kind: "agent" },
  { name: "far-hub", kind: "relay" }, { name: "solo", kind: "robot" }, { name: "lost", kind: "robot" },
];
const LOCATIONS = {
  members: [
    { name: "hub-1", best: { source: "declared", label: "Lab", lat: 10, lon: 20 } },
    { name: "rover-1", best: { source: "declared", label: "Lab", lat: 10, lon: 20 } },
    { name: "helper", best: { source: "network", label: "Cloud City", lat: 40, lon: -100, accuracy_km: 100 } },
    { name: "far-hub", best: { source: "network", label: "Far Town", lat: -5, lon: 30, accuracy_km: 20 } },
    { name: "solo", best: { source: "gps", lat: 1, lon: 2 } },
    { name: "lost", best: null, unplaced: "Only a local network address (10.0.0.50) has been seen, which can't be located." },
  ],
  geo: { enabled: true },
  server: { source: "declared", label: "Server room", lat: 30, lon: 0 },
};

function geo(counts = {}) {
  return buildGeography({ locations: LOCATIONS, swarms: SWARMS, members: MEMBERS, counts });
}

describe("projection and zoom clamps", () => {
  it("clamps zoom to 0.9×–30×", () => {
    expect(MIN_ZOOM).toBe(0.9);
    expect(MAX_ZOOM).toBe(30);
    expect(clampZoom(0.1)).toBe(0.9);
    expect(clampZoom(100)).toBe(30);
    expect(clampZoom(7)).toBe(7);
    expect(clampZoom(NaN)).toBe(0.9);
  });
  it("draws land-110m below 4× and land-50m above", () => {
    expect(landDetail(1)).toBe("110m");
    expect(landDetail(4)).toBe("110m");
    expect(landDetail(4.1)).toBe("50m");
    expect(globeRadius(800, 400, 2)).toBeCloseTo(400 * 0.36 * 2);
  });
  it("puts the view centre in the middle, and lifts raised points outward", () => {
    const rot = { lon: 20, lat: 10 };
    const p = project(20, 10, rot, 100, 200, 150);
    expect(p).toEqual({ x: 200, y: 150, visible: true });
    const a = project(30, 10, rot, 100, 200, 150);
    const b = projectAlt(30, 10, 0.5, rot, 100, 200, 150);
    expect(b.x - 200).toBeCloseTo((a.x - 200) * 1.5);
    // Behind the globe but lifted past the limb: visible.
    expect(project(115, 10, rot, 100, 200, 150).visible).toBe(false);
    expect(projectAlt(115, 10, 0.5, rot, 100, 200, 150).visible).toBe(true);
  });
  it("closes rings that cross the horizon along the limb, and culls rings out of view", () => {
    const rot = { lon: 0, lat: 0 };
    const pts = limbRing([[80, 0], [100, 0], [100, 10], [80, 10]], rot, 100, 0, 0);
    for (const q of pts) expect(Math.hypot(q.x, q.y)).toBeLessThanOrEqual(100 + 1e-9);
    expect(limbRing([[170, 0], [175, 0], [175, 5]], rot, 100, 0, 0)).toBeNull();
    const caps = ringCaps([[[0, 0], [1, 0], [1, 1]], [[170, 0], [171, 0], [171, 1]]]);
    expect(visibleCaps(caps, rot, 90)).toHaveLength(1);
    expect(visibleCaps(caps, { lon: 170, lat: 0 }, 5)).toHaveLength(1);
  });
});

describe("zooming into a swarm", () => {
  it("fades the system in as z = smoothstep(5, 12, zoom)", () => {
    expect(systemFade(1)).toBe(0);
    expect(systemFade(5)).toBe(0);
    expect(systemFade(12)).toBe(1);
    expect(systemFade(30)).toBe(1);
    for (const zoom of [5.5, 7, 8.5, 10, 11.9]) expect(systemFade(zoom)).toBeCloseTo(smoothstep(5, 12, zoom), 12);
    expect(systemFade(8.5)).toBeCloseTo(0.5, 12);
  });
  it("eases a move over 900 ms, or jumps with reduced motion", () => {
    const cam = createGlobeCamera({ lon: 0, lat: 0, zoom: 1 }, 0);
    cam.moveTo({ lon: 20, lat: 10, zoom: 14 }, 0);
    cam.step(EASE_MS / 2);
    expect(cam.view.zoom).toBeGreaterThan(1);
    expect(cam.view.zoom).toBeLessThan(14);
    cam.step(EASE_MS);
    expect(cam.view).toEqual({ lon: 20, lat: 10, zoom: 14 });
    expect(cam.moving).toBe(false);
    const quick = createGlobeCamera({ lon: 0, lat: 0, zoom: 1 }, 0);
    quick.moveTo({ zoom: 50 }, 0, { instant: true });
    expect(quick.view.zoom).toBe(30);
  });
  it("turns the short way round the date line", () => {
    const cam = createGlobeCamera({ lon: 170, lat: 0, zoom: 1 }, 0);
    cam.moveTo({ lon: -170 }, 0);
    cam.step(EASE_MS / 2);
    expect(Math.abs(cam.view.lon)).toBeGreaterThan(170);
  });
  it("drifts at 0.3°/s only after 20 s without input, and never in Calm view", () => {
    const cam = createGlobeCamera({ lon: 0, lat: 0, zoom: 1 }, 0);
    cam.step(IDLE_MS - 1000);
    expect(cam.view.lon).toBe(0);
    cam.step(IDLE_MS + 9000);
    expect(Math.abs(cam.view.lon)).toBeCloseTo(0.3 * 10, 6);
    const lon = cam.view.lon;
    cam.step(IDLE_MS + 19000, { animate: false });
    expect(cam.view.lon).toBe(lon);
    cam.drag(10, 0, 100, IDLE_MS + 20000);
    const after = cam.view.lon;
    cam.step(IDLE_MS + 25000);
    expect(cam.view.lon).toBe(after); // input resets the idle timer
  });
});

describe("the pyramid on the frame edge", () => {
  it("stays where it is inside the frame", () => {
    expect(pinToEdge({ x: 300, y: 200 }, 800, 400)).toEqual({ x: 300, y: 200, edge: false, arrow: null });
  });
  it("pins to the edge on the line from the centre, with an arrow toward it", () => {
    const right = pinToEdge({ x: 2000, y: 200 }, 800, 400, 46);
    expect(right).toEqual({ x: 800 - 46, y: 200, edge: true, arrow: "→" });
    const up = pinToEdge({ x: 400, y: -900 }, 800, 400, 46);
    expect(up.y).toBeCloseTo(46);
    expect(up.x).toBeCloseTo(400);
    expect(up.arrow).toBe("↑");
    const corner = pinToEdge({ x: -1000, y: 1000 }, 800, 400, 46);
    expect(corner.edge).toBe(true);
    expect(corner.y).toBeCloseTo(400 - 46); // the nearer edge wins
    expect(corner.x).toBeGreaterThanOrEqual(46);
    expect(corner.arrow).toBe("↙");
  });
  it("pins a hidden place (behind the globe) even when it projects inside", () => {
    const hidden = pinToEdge({ x: 500, y: 200 }, 800, 400, 46, true);
    expect(hidden.edge).toBe(true);
    expect(hidden.x).toBeCloseTo(800 - 46);
    expect(pinToEdge({ x: 400, y: 200 }, 800, 400, 46, true)).toMatchObject({ x: 400, y: 46, edge: true });
    expect(arrowFor(-1, 0)).toBe("←");
  });
});

describe("places on the globe", () => {
  it("draws a network estimate as a disc of its accuracy radius, a declared place as a point", () => {
    const g = geo();
    const home = g.lights.find((l) => l.members.includes("hub-1"));
    const far = g.lights.find((l) => l.members.includes("far-hub"));
    expect(markOf(home)).toEqual({ shape: "point" });
    expect(markOf(far)).toEqual({ shape: "disc", radiusKm: 20, dashed: true });
    expect(far.estimated).toBe(true);
    expect(lightNarration(far)).toMatch(/Estimated place: Far Town, within about 20 km/);
    expect(discPx(637.1, 100)).toBeCloseTo(10);
    expect(discPx(1, 100)).toBe(6);
  });
  it("puts members at one address in one light, and joins a swarm's sites with a line", () => {
    const g = geo({ "hub-1": 10, "rover-1": 5 });
    const lab = g.lights.filter((l) => l.swarm === "lab");
    expect(lab).toHaveLength(2);
    expect(lab[0].members).toEqual(["hub-1", "rover-1"]); // the home light first
    expect(lab[0].records).toBe(15);
    expect(g.links).toEqual([[lab[0].id, lab[1].id]]);
    expect(g.lights.find((l) => l.label === "solo").source).toBe("gps");
    expect(lightRadius(0)).toBeLessThan(lightRadius(10));
    expect(lightRadius(1000) - lightRadius(100)).toBeCloseTo(lightRadius(100) - lightRadius(10), 0); // log
    expect(lightRadius(1e12)).toBe(30);
  });
  it("makes a cloud agent a satellite, not a light", () => {
    const g = geo();
    expect(g.satellites).toEqual([{ id: "sat:helper", name: "helper", swarm: "lab", records: 0 }]);
    expect(g.lights.some((l) => l.members.includes("helper"))).toBe(false);
    expect(isSatellite({ kind: "agent" }, null)).toBe(true);
    expect(isSatellite({ kind: "assistant" }, { source: "declared", lat: 1, lon: 1 })).toBe(false); // declared: on the map
    expect(isSatellite({ kind: "robot" }, null)).toBe(false);
    const t = globeTouchables(g, LOCATIONS.server).find((x) => x.id === "sat:helper");
    expect(t.narration).toBe("helper, a cloud agent: a satellite. Its place is its provider's.");
  });
  it("gathers unplaced members with their reason, never an address", () => {
    const g = geo();
    expect(g.unplaced).toEqual([{ id: "unplaced:lost", name: "lost", swarm: null,
      reason: "Only a local network address has been seen, which can't be located." }]);
    for (const t of globeTouchables(g, LOCATIONS.server)) expect(`${t.label} ${t.narration}`).not.toMatch(/\d+\.\d+\.\d+\.\d+/);
  });
  it("takes addresses out of text", () => {
    expect(redactAddresses("Its address (203.0.113.9) isn't in the city database")).toBe("Its address isn't in the city database");
    expect(redactAddresses("seen at 192.168.1.4 today")).toBe("seen at an address today");
    expect(redactAddresses("from (2001:db8::1) and fe80::1")).toBe("from and an address");
    expect(redactAddresses("9:02 PM · rover-1 picked up")).toBe("9:02 PM · rover-1 picked up");
    expect(redactAddresses(null)).toBeNull();
  });
});

describe("the table twin", () => {
  it("has a row per place, the satellites and LTMS, counting records up to the hand", () => {
    const events = [{ t: 1, member: "hub-1" }, { t: 2, member: "rover-1" }, { t: 3, member: "helper" }, { t: 9, member: "hub-1" }];
    const rows = placeRows(geo(), LOCATIONS.server, countsUpTo(events, 5));
    expect(rows.map((r) => [r.site, r.members, r.source, r.records])).toEqual([
      ["lab · Lab", "hub-1, rover-1", "declared", 2],
      ["lab · Far Town", "far-hub", "network, estimated (±20 km)", 0],
      ["solo", "solo", "GPS", 0],
      ["Cloud (satellites)", "helper", "provider's place", 1],
      ["LTMS", "—", "declared", null],
    ]);
    expect(placeRows(geo(), null).at(-1).source).toBe("not declared");
  });
});
