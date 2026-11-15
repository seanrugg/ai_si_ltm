import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import Places from "./Places.jsx";
import { api } from "../../api/client.js";
import { actionPoses, hasBody, makeScaler, niceLength, poseFrames, poseSamples } from "../../lib/profile.js";

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
    render(React.createElement(Places, { subject: "member", name: "z3-r0", members: [{ name: "z3-r0", kind: "robot" }], swarms: [],
      summary: { places: [{ place: "kitchen", total: 4, sense: 2, act: 2, comm: 0, learn: 0, action_success: 0.5 }] } }));
    expect(await screen.findByText(/No pose data yet/)).toBeDefined();
    expect(screen.getByText("4 records · 50% action success")).toBeDefined();
    expect(await screen.findByText("Lab, B12")).toBeDefined();
    expect(screen.getByText(/High precision/)).toBeDefined(); // GPS row: explanation only
    const sensed = vi.mocked(api).mock.calls.find(([p, o]) => p === "/admin/timeline" && o.params.type === "sensed");
    expect(sensed[1].params).toEqual({ agent: "z3-r0", type: "sensed", limit: 200 });
  });
});

describe("Choreography (#407)", () => {
  const summary = { places: [] };
  function mockApi(sensed = []) {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (path, opts) => {
      if (path === "/admin/timeline") return { records: opts.params.type === "sensed" ? sensed : [] };
      if (path === "/admin/locations") return { members: [] };
      throw new Error(path);
    });
  }

  it("is shown, with its empty state, for a robot", async () => {
    mockApi();
    render(React.createElement(Places, { subject: "member", name: "bot-1", summary, swarms: [],
      members: [{ name: "bot-1", kind: "robot", cognition: { value: "offboard" } }] }));
    expect(screen.getByRole("heading", { name: /Choreography/ })).toBeDefined();
    expect(await screen.findByText(/Members with a body send movement/)).toBeDefined();
    expect(screen.queryByText(/^Movement$/)).toBeNull();
  });

  it("is hidden for AI agents and hubs, without even the empty state", async () => {
    mockApi();
    render(React.createElement(Places, { subject: "member", name: "helper", summary, swarms: [],
      members: [{ name: "helper", kind: "agent" }] }));
    await screen.findByText("Places");
    expect(screen.queryByText(/Choreography/)).toBeNull();
    expect(screen.queryByText(/No pose data yet/)).toBeNull();
    cleanup();
    render(React.createElement(Places, { subject: "member", name: "hub-1", summary,
      swarms: [{ name: "lab", members: [{ name: "hub-1", role: "hub" }] }],
      members: [{ name: "hub-1", kind: "robot" }] }));
    await screen.findByText("Places");
    expect(screen.queryByText(/Choreography/)).toBeNull();
    expect(vi.mocked(api).mock.calls.some(([p]) => p === "/admin/timeline")).toBe(false);
  });

  it("knows which members have a body", () => {
    expect(hasBody({ name: "a", kind: "robot" })).toBe(true); // Physical AI
    expect(hasBody({ name: "a", kind: "robot", cognition: { value: "offboard" } })).toBe(true); // Hybrid robot
    expect(hasBody({ name: "a", kind: "assistant" })).toBe(false);
    expect(hasBody({ name: "a", kind: "relay" })).toBe(false);
    expect(hasBody({ name: "a", kind: "robot", role: "hub" })).toBe(false);
    expect(hasBody(null)).toBe(false);
  });

  const pose = (ts, frame, samples) => ({ agent: "bot-1", ts, payload: { kind: "pose", frame, unit: "mm", samples } });
  const records = [
    pose("2026-11-14T10:00:00Z", "vector-odom:1", [{ t: 0, x: 0, y: 0 }, { t: 1, x: 50, y: 0 }]),
    pose("2026-11-14T10:05:00Z", "vector-odom:2", [{ t: 0, x: 0, y: 0 }, { t: 2, x: 0, y: 80, theta: 1 }]),
    pose("2026-11-14T10:00:30Z", "vector-odom:1", [{ t: 0, x: 60, y: 10 }]),
  ];

  it("lists frames newest first, never joining them", () => {
    const tracks = poseFrames(records);
    expect(tracks.map((t) => t.frame)).toEqual(["vector-odom:2", "vector-odom:1"]);
    expect(tracks[1].samples.map((s) => [s.x, s.y])).toEqual([[0, 0], [50, 0], [60, 10]]);
    expect(tracks[0].unit).toBe("mm");
    expect(tracks[0].newFrame).toBe(true);
    expect(tracks[1].newFrame).toBe(false);
    expect(niceLength(23)).toBe(20);
    expect(niceLength(0.7)).toBe(0.5);
  });

  it("draws one panel per frame, newest first, with a scale bar in the frame's unit", async () => {
    mockApi(records);
    render(React.createElement(Places, { subject: "member", name: "bot-1", summary, swarms: [],
      members: [{ name: "bot-1", kind: "robot" }] }));
    const panels = await screen.findAllByRole("img", { name: /in frame vector-odom/ });
    expect(panels.map((p) => p.getAttribute("aria-label").match(/frame (\S+):/)[1]))
      .toEqual(["vector-odom:2", "vector-odom:1"]);
    expect(screen.getByText(/pose origin changed/)).toBeDefined();
    expect(screen.getAllByText(/^\d+(\.\d+)? mm$/).length).toBe(2); // one scale bar per panel
  });
});
