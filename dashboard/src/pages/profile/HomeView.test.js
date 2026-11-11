import { describe, expect, it } from "vitest";
import { flowWidth, roomsOf, sitesOf } from "../../lib/sites.js";
import { homeLayout } from "./HomeView.jsx";

const locs = [
  { name: "z3-r0", best: { source: "declared", label: "Home", lat: 1, lon: 2 }, all: [{ source: "declared", label: "Home", room: "Lab" }] },
  { name: "vector", best: { source: "declared", label: "Home", lat: 1, lon: 2 }, all: [{ source: "declared", label: "Home", room: "Kitchen" }] },
  { name: "jetson-hub", best: { source: "declared", label: "Home", lat: 1, lon: 2 }, all: [{ source: "declared", label: "Home", room: "Lab" }] },
  { name: "dx-1", best: { source: "declared", label: "Home", lat: 1, lon: 2 }, all: [{ source: "declared", label: "Home" }] },
  { name: "cloud-agent", best: { source: "network", label: "City", lat: 40, lon: -75, accuracy_km: 20 }, all: [] },
  { name: "nowhere", best: null, all: [] },
];

describe("rooms and sites", () => {
  it("puts members into their rooms, and members without one into Unassigned", () => {
    const rooms = roomsOf(locs, ["z3-r0", "vector", "jetson-hub", "dx-1"]);
    expect(rooms).toEqual([
      { room: "Kitchen", members: ["vector"] },
      { room: "Lab", members: ["jetson-hub", "z3-r0"] },
      { room: "Unassigned", members: ["dx-1"] },
    ]);
    const { boxes, positions } = homeLayout(rooms);
    expect(boxes.map((b) => b.room)).toEqual(["Kitchen", "Lab", "Unassigned"]);
    const lab = boxes[1];
    for (const n of ["jetson-hub", "z3-r0"]) {
      expect(positions[n].x).toBeGreaterThan(lab.x);
      expect(positions[n].x).toBeLessThan(lab.x + lab.w);
    }
  });
  it("groups declared members into one home site; others are their own site", () => {
    const sites = sitesOf(locs, [{ name: "cloud-agent", kind: "agent" }]);
    expect(sites.map((s) => [s.label, s.members.length, s.home, s.note])).toEqual([
      ["Home", 4, true, "declared"],
      ["cloud-agent", 1, false, "data-center estimate"],
    ]);
  });
});

describe("flowWidth", () => {
  it("stays within 1.2-5 px", () => {
    for (const [r, max] of [[0, 10], [0.001, 10], [5, 10], [10, 10], [500, 10], [3, 0]]) {
      const w = flowWidth(r, max);
      expect(w).toBeGreaterThanOrEqual(1.2);
      expect(w).toBeLessThanOrEqual(5);
    }
    expect(flowWidth(10, 10)).toBe(5);
  });
});
