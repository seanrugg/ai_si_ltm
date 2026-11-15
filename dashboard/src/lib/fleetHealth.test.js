import { describe, expect, it } from "vitest";
import {
  FREE, STATUS_TEXT, filterRows, groupRows, hasHealthWarning, healthSummary, intervalText, sortRows,
} from "./fleetHealth.js";

const hubHealth = {
  ts: "2026-01-01T12:00:00Z", summary: "machine health", throttle_events_24h: 1,
  payload: { temp_c: { cpu: { last: 55 }, gpu: { last: 71.2 } }, services: { "wire-pod": "active" } },
};
const rows = [
  { id: "1", name: "zed", swarm: "alpha", status: "on_time", last_heard: "2026-01-01T12:00:00Z", health: null },
  { id: "2", name: "amy", swarm: "beta", status: "silent", last_heard: "2026-01-01T08:00:00Z", health: null },
  { id: "3", name: "hub", swarm: "alpha", status: "late", last_heard: "2026-01-01T11:00:00Z", health: hubHealth },
  { id: "4", name: "solo", swarm: null, status: "never", last_heard: null, health: null },
];

describe("fleet health helpers (#381)", () => {
  it("summarises a health report in one line, and nothing when never reported", () => {
    expect(healthSummary(hubHealth)).toBe("gpu 71 °C, 1 throttle event in 24 h, wire-pod up");
    expect(healthSummary(null)).toBeNull();
    expect(healthSummary({ summary: "ok", payload: {} })).toBe("ok");
    expect(healthSummary({ throttle_events_24h: 2, payload: { services: { x: "failed" } } }))
      .toBe("2 throttle events in 24 h, x failed");
  });

  it("flags throttling and stopped services as health warnings", () => {
    expect(hasHealthWarning(hubHealth)).toBe(true);
    expect(hasHealthWarning({ throttle_events_24h: 0, payload: { services: { a: "active" } } })).toBe(false);
    expect(hasHealthWarning({ payload: { services: { a: "inactive" } } })).toBe(true);
    expect(hasHealthWarning(null)).toBe(false);
  });

  it("says every status in words", () => {
    for (const s of ["on_time", "late", "silent", "unknown", "never"]) expect(STATUS_TEXT[s]).toBeTruthy();
    expect(intervalText(43200)).toBe("every 12 h");
    expect(intervalText(30)).toBe("every 30 s");
    expect(intervalText(null)).toBeNull();
  });

  it("filters: late or silent, health warnings, one swarm, free agents", () => {
    const names = (rs) => rs.map((r) => r.name).sort();
    expect(names(filterRows(rows, "late"))).toEqual(["amy", "hub"]);
    expect(names(filterRows(rows, "health"))).toEqual(["hub"]);
    expect(names(filterRows(rows, "all", "alpha"))).toEqual(["hub", "zed"]);
    expect(names(filterRows(rows, "all", FREE))).toEqual(["solo"]);
    expect(filterRows(rows, "all")).toHaveLength(4);
  });

  it("sorts by name, last heard (never last) and status (worst first)", () => {
    expect(sortRows(rows, "name").map((r) => r.name)).toEqual(["amy", "hub", "solo", "zed"]);
    expect(sortRows(rows, "heard").map((r) => r.name)).toEqual(["zed", "hub", "amy", "solo"]);
    expect(sortRows(rows, "status").map((r) => r.name)).toEqual(["amy", "hub", "solo", "zed"]);
  });

  it("groups by swarm with free agents last, keeping the sort inside each group", () => {
    const g = groupRows(sortRows(rows, "name"));
    expect(g.map((x) => x.swarm)).toEqual(["alpha", "beta", null]);
    expect(g[0].rows.map((r) => r.name)).toEqual(["hub", "zed"]);
  });
});
