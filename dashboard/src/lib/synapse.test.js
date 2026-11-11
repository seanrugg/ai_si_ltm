import { describe, expect, it } from "vitest";
import { THREAD_GAP, layout, strands, threadCount } from "./synapse.js";

describe("threadCount", () => {
  it("steps at 10, 50, 200 and 1000", () => {
    expect([0, 1, 9, 10, 49, 50, 199, 200, 999, 1000, 50000].map(threadCount))
      .toEqual([0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]);
  });
});

const nodes = [
  { name: "vector", role: "free" },
  { name: "z3-r0", role: "member", swarm: "echo-base" },
  { name: "jetson-hub", role: "hub", swarm: "echo-base", is_hub: true },
  { name: "dx-1", role: "member", swarm: "echo-base" },
];

describe("layout", () => {
  it("puts the member, or the swarm's hub, in the centre", () => {
    const m = layout(nodes, { type: "member", name: "z3-r0" }, { width: 400, height: 400 });
    expect(m.centre).toBe("z3-r0");
    expect(m.positions["z3-r0"]).toEqual({ x: 200, y: 200, centre: true });
    expect(layout(nodes, { type: "swarm", name: "echo-base" }).centre).toBe("jetson-hub");
  });
  it("is deterministic, whatever the input order", () => {
    const a = layout(nodes, { type: "member", name: "z3-r0" });
    const b = layout([...nodes].reverse(), { type: "member", name: "z3-r0" });
    expect(a).toEqual(b);
    // ring order: role, then name -> the hub first, at the top
    expect(a.positions["jetson-hub"].y).toBeLessThan(a.positions["z3-r0"].y);
  });
});

describe("strands", () => {
  it("lays out each category's threads side by side with distinct offsets", () => {
    const { positions } = layout(nodes, { type: "member", name: "z3-r0" });
    const edges = [
      { a: "vector", b: "z3-r0", category: "comm", count: 42 },  // 2 threads
      { a: "vector", b: "z3-r0", category: "learn", count: 3 },  // 1 thread
      { a: "dx-1", b: "z3-r0", category: "act", count: 250 },    // 4 threads
      { a: "ghost", b: "z3-r0", category: "act", count: 5 },     // no position: skipped
    ];
    const out = strands(edges, positions);
    const pair = out.filter((t) => t.a === "vector");
    expect(pair).toHaveLength(3);
    expect(new Set(pair.map((t) => t.offset)).size).toBe(3);
    expect(pair.map((t) => t.offset)).toEqual([-THREAD_GAP, 0, THREAD_GAP]);
    expect(pair.filter((t) => t.primary).map((t) => t.category)).toEqual(["comm", "learn"]);
    expect(out.filter((t) => t.a === "dx-1")).toHaveLength(4);
    expect(out.every((t) => t.d.startsWith("M") && t.d.includes("Q"))).toBe(true);
    expect(strands(edges, positions)).toEqual(out); // deterministic
  });
});
