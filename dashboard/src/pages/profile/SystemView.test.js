import { describe, expect, it } from "vitest";
import {
  audibleMember, createSystemMotion, defaultGeometry, drawSystem, narrationOf, systemHeartbeats,
} from "./SystemView.jsx";
import { buildScene, layoutSystem } from "../../lib/topology.js";

const SINCE = "2026-11-13T00:00:00+00:00";
const T0 = Date.parse(SINCE);
const episodes = {
  "rover-1": { since: SINCE, pulses: [
    [10, 0.4, "perception", "sense", "camera", "saw a face"],
    [11, 0.2, "perception", "self", "battery", "health"],
    [12, 0.2, "communication", "comm", "hub", "told rover-2"],
  ], lanes: {} },
};
const roster = [{ name: "hub-1", role: "hub" }, { name: "rover-1", role: "member" }, { name: "rover-2", role: "member" }];
const edges = [{ a: "rover-1", b: "rover-2", category: "comm", count: 3 }];

function setup() {
  const scene = buildScene({ roster, episodes });
  const geo = defaultGeometry(640, 420);
  const layout = layoutSystem(scene, { ...geo, still: true });
  return { scene, layout, ltms: geo.ltmsTarget };
}

describe("system motion", () => {
  it("turns records reaching the hand into travelling lights, then flashes the pyramid", () => {
    const { scene, layout, ltms } = setup();
    const m = createSystemMotion(scene, { edges });
    m.advance(T0, 0, { animate: true, layout, ltms }); // primes at the hand
    const fresh = m.advance(T0 + 13_000, 100, { animate: true, layout, ltms, windowMs: 60_000 });
    expect(fresh).toHaveLength(3);
    // camera, self-report, the message (+ its arc to rover-2)
    expect(m.flights).toHaveLength(4);
    expect(m.flights[0].points).toHaveLength(4); // moon → planet → star → pyramid
    expect(m.flights[0].life).toBeCloseTo(960, 6);
    expect(m.flights.filter((f) => f.arc)).toHaveLength(1);
    expect(m.beats["rover-1"]).toBe(100); // the self-report is a heartbeat
    expect(m.lit(100)["moon:rover-1:camera"]).toBe(1);
    m.advance(T0 + 13_000, 5000, { animate: true, layout, ltms });
    expect(m.flights).toHaveLength(0);
    expect(m.flasher.flashes.length).toBe(1); // landed together: one merged flash
  });

  it("in Calm view, records don't travel: their source and the pyramid step brighter", () => {
    const { scene, layout, ltms } = setup();
    const m = createSystemMotion(scene, { edges });
    m.advance(T0, 0, { animate: false, layout, ltms });
    m.advance(T0 + 10_500, 100, { animate: false, layout, ltms, windowMs: 60_000 });
    expect(m.flights).toHaveLength(0);
    const lit = m.lit(200);
    expect(lit.ltms).toBe(1);
    expect(lit["moon:rover-1:camera"]).toBeGreaterThan(0);
    expect(m.lit(1200).ltms).toBeUndefined(); // a step, held for a second, not a fade
  });

  it("lands old records without travel after a seek, and resets when the hand goes back", () => {
    const { scene, layout, ltms } = setup();
    const m = createSystemMotion(scene, { edges });
    m.advance(T0, 0, { animate: true, layout, ltms });
    m.advance(T0 + 3600_000, 10, { animate: true, layout, ltms, windowMs: 3000 });
    expect(m.flights).toHaveLength(0);
    m.advance(T0 + 9_000, 20, { animate: true, layout, ltms, windowMs: 3000 });
    m.advance(T0 + 10_100, 30, { animate: true, layout, ltms, windowMs: 3000 });
    expect(m.flights).toHaveLength(1);
  });

  it("narrates records but not self-reports", () => {
    expect(narrationOf({ member: "rover-1", category: "sense", summary: "saw a face" })).toBe("rover-1: saw a face");
    expect(narrationOf({ member: "rover-1", category: "self", summary: "health" })).toBe(null);
  });

  it("draws on a 2D context without throwing (still and moving)", () => {
    const { scene, layout, ltms } = setup();
    const calls = [];
    const ctx = new Proxy({}, {
      get: (target, k) => (k in target ? target[k] : () => {
        calls.push(k);
        return k === "createLinearGradient" ? { addColorStop() {} } : undefined;
      }),
      set: (target, k, v) => { target[k] = v; return true; },
    });
    for (const still of [true, false]) {
      drawSystem(ctx, scene, layout, { nowMs: 1000, tHand: T0, still, ltms, focus: "rover-1",
                                       flights: [{ points: [layout.planets["rover-1"], ltms], color: "#fff", born: 0, life: 2000, lift: 0 }] });
    }
    expect(calls).toContain("ellipse");
    expect(calls).toContain("fillText");
  });
});

/** A fake sound engine: records the calls, plays nothing (#428). */
function fakeEngine() {
  const calls = [];
  return {
    calls,
    pulse: (category, opts) => { calls.push(["pulse", category, opts]); return true; },
    note: (key) => { calls.push(["note", key]); return true; },
    heartbeat: (key, info, opts) => { calls.push(["heartbeat", key, info, opts]); return { swell: opts?.audible ? 0.5 : 0.25 }; },
    forget: (key) => { calls.push(["forget", key]); },
  };
}

describe("Topology sound (#428)", () => {
  it("pulses each record's category as its light lands on the pyramid, not self-reports or arcs", () => {
    const { scene, layout, ltms } = setup();
    const m = createSystemMotion(scene, { edges });
    const landed = [];
    const onLand = (c) => landed.push(c);
    m.advance(T0, 0, { animate: true, layout, ltms, onLand });
    m.advance(T0 + 13_000, 100, { animate: true, layout, ltms, windowMs: 60_000, onLand });
    expect(landed).toEqual([]); // still travelling
    m.advance(T0 + 13_000, 5000, { animate: true, layout, ltms, onLand });
    expect(landed.sort()).toEqual(["comm", "sense"]);
  });

  it("doesn't pulse records landed by a seek or in Calm view", () => {
    const { scene, layout, ltms } = setup();
    const landed = [];
    const onLand = (c) => landed.push(c);
    const seek = createSystemMotion(scene, { edges });
    seek.advance(T0, 0, { animate: true, layout, ltms, onLand });
    seek.advance(T0 + 3600_000, 10, { animate: true, layout, ltms, windowMs: 3000, onLand });
    seek.advance(T0 + 3600_000, 9000, { animate: true, layout, ltms, onLand });
    const calm = createSystemMotion(scene, { edges });
    calm.advance(T0, 0, { animate: false, layout, ltms, onLand });
    calm.advance(T0 + 13_000, 100, { animate: false, layout, ltms, windowMs: 60_000, onLand });
    expect(landed).toEqual([]);
  });

  it("tracks each member's last self-report in record time, also after a seek", () => {
    const { scene, layout, ltms } = setup();
    const m = createSystemMotion(scene, { edges });
    m.advance(T0 + 20_000, 0, { animate: true, layout, ltms }); // primes past the self-report
    expect(m.lastSelf["rover-1"]).toBe(T0 + 11_000);
    m.advance(T0 + 5_000, 10, { animate: true, layout, ltms }); // back before it
    expect(m.lastSelf["rover-1"]).toBeUndefined();
  });

  it("gives every planet a heartbeat, with one audible: the selected planet, else the profile's member", () => {
    const { scene, layout, ltms } = setup();
    const m = createSystemMotion(scene, { edges });
    m.advance(T0 + 20_000, 0, { animate: true, layout, ltms });
    const engine = fakeEngine();
    const audible = audibleMember("planet:rover-2", scene);
    const { swells, keys } = systemHeartbeats(engine, scene, m, { tHand: T0 + 20_000, audible });
    const beats = engine.calls.filter((c) => c[0] === "heartbeat");
    expect(beats.map((c) => c[1]).sort()).toEqual([...scene.order].sort());
    expect(beats.filter((c) => c[3].audible).map((c) => c[1])).toEqual(["rover-2"]);
    const r1 = beats.find((c) => c[1] === "rover-1");
    expect(r1[2].sinceLastSec).toBe(9); // 20 s hand − 11 s self-report
    expect(swells["rover-2"]).toBe(0.5);
    expect(keys.sort()).toEqual([...scene.order].sort());
  });

  it("picks the audible member: planet, a moon's planet, else the focus; hubs never", () => {
    expect(audibleMember("planet:rover-1", { focus: "x" })).toBe("rover-1");
    expect(audibleMember("moon:rover-1:camera", { focus: "x" })).toBe("rover-1");
    expect(audibleMember(null, { focus: "rover-2" })).toBe("rover-2");
    expect(audibleMember("star:hub-1", { focus: null })).toBe(null);
    expect(audibleMember(null, { focus: null })).toBe(null);
  });

  it("draws planets with the engine's swell when given", () => {
    const { scene, layout, ltms } = setup();
    const radii = [];
    const ctx = new Proxy({}, {
      get: (target, k) => (k in target ? target[k] : (...args) => {
        if (k === "arc") radii.push(args[2]);
        return k === "createLinearGradient" || k === "createRadialGradient" ? { addColorStop() {} } : undefined;
      }),
      set: (target, k, v) => { target[k] = v; return true; },
    });
    drawSystem(ctx, scene, layout, { nowMs: 1000, tHand: T0, still: false, ltms, swells: {} });
    const flat = radii.slice();
    radii.length = 0;
    drawSystem(ctx, scene, layout, { nowMs: 1000, tHand: T0, still: false, ltms, swells: { "rover-1": 1 } });
    expect(radii).not.toEqual(flat);
  });
});
