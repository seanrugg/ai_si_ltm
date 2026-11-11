import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import Places from "./Places.jsx";
import { api } from "../../api/client.js";
import { actionPoses, makeScaler, poseSamples } from "../../lib/profile.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

describe("makeScaler", () => {
  it("maps samples into the viewBox with 10% padding, y flipped", () => {
    const pts = [{ x: 0, y: 0 }, { x: 10, y: 5 }, { x: 4, y: 2 }];
    const s = makeScaler(pts, 200, 100);
    for (const p of pts.map(s.map)) {
      expect(p.x).toBeGreaterThanOrEqual(20 - 1e-9);
      expect(p.x).toBeLessThanOrEqual(180 + 1e-9);
      expect(p.y).toBeGreaterThanOrEqual(10 - 1e-9);
      expect(p.y).toBeLessThanOrEqual(90 + 1e-9);
    }
    expect(s.map({ x: 0, y: 0 }).y).toBeGreaterThan(s.map({ x: 0, y: 5 }).y); // up is up
    expect(s.map({ x: 10, y: 0 }).x - s.map({ x: 0, y: 0 }).x).toBeCloseTo(160); // the wider axis fills
  });
  it("handles a single point", () => {
    const p = makeScaler([{ x: 3, y: 3 }], 100, 100).map({ x: 3, y: 3 });
    expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
  });
});

describe("pose helpers", () => {
  it("reads pose samples and posed actions", () => {
    const recs = [{ agent: "z3-r0", ts: "2026-11-10T12:00:00Z",
      payload: { kind: "pose", frame: "map", samples: [{ t: 0, x: 1, y: 2 }, { t: 0.5, v: { x: 1.5, y: 2 } }] } },
    { agent: "z3-r0", ts: "2026-11-10T12:00:01Z", payload: { kind: "sensor", samples: [{ t: 0, v: 3 }] } }];
    expect(poseSamples(recs).map((p) => [p.x, p.y, p.frame])).toEqual([[1, 2, "map"], [1.5, 2, "map"]]);
    expect(actionPoses([{ agent: "a", ts: "t", payload: { pose: { x: 1, y: 1 } }, outcome: "stuck" },
      { agent: "a", ts: "t", payload: {} }])).toHaveLength(1);
  });
});

describe("Places tab", () => {
  it("with no pose data, explains the convention", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/admin/timeline") return { records: [] };
      if (path === "/admin/locations") return { members: [{ name: "z3-r0", best: null, all: [
        { source: "declared", label: "Lab", room: "B12", lat: null, lon: null, accuracy_km: null }] }] };
      throw new Error(path);
    });
    render(React.createElement(Places, { subject: "member", name: "z3-r0", members: [], swarms: [],
      summary: { places: [{ place: "kitchen", total: 4, sense: 2, act: 2, comm: 0, learn: 0, action_success: 0.5 }] } }));
    expect(await screen.findByText(/No pose data yet/)).toBeDefined();
    expect(screen.getByText("4 records · 50% action success")).toBeDefined();
    expect(await screen.findByText("Lab, B12")).toBeDefined();
    expect(screen.getByText(/High precision/)).toBeDefined(); // GPS row: explanation only
    const sensed = vi.mocked(api).mock.calls.find(([p, o]) => p === "/admin/timeline" && o.params.type === "sensed");
    expect(sensed[1].params).toEqual({ agent: "z3-r0", type: "sensed", limit: 200 });
  });
});
