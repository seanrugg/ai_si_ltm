import { describe, expect, it } from "vitest";
import { createSystemMotion, defaultGeometry, drawSystem, narrationOf } from "./SystemView.jsx";
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
