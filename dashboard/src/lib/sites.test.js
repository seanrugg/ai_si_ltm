import { describe, expect, it } from "vitest";
// Was HomeView.test.js; the Home view is replaced by the star system on the globe (#405).
import { flowWidth, roomsOf, sitesOf } from "./sites.js";

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
