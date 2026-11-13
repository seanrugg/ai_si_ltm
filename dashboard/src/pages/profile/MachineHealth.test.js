import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { HealthView } from "./MachineHealth.jsx";
import { healthReports, hottestSeries, latestRows } from "../../lib/health.js";

afterEach(() => cleanup());

const rec = (ts, cpuMax, extra = {}) => ({
  ts, tags: ["telemetry", "internal", "health"],
  payload: { sensor: "jetson-health", temp_c: { cpu: { min: 40, max: cpuMax, last: cpuMax } },
    cpu_pct: { min: 1, max: 9, last: 4 }, ram_used_mib: { min: 3000, max: 3900, last: 3800 }, ram_total_mib: 7619,
    power_mode: "25W", oc_events: { oc3: 7 }, throttled: false, ...extra },
});

describe("machine health (#349)", () => {
  it("keeps only health reports, oldest first", () => {
    const rs = healthReports([rec("2026-11-12T02:00:00Z", 50), { ts: "x", tags: ["telemetry", "internal"], payload: { uptime_s: 1 } },
      rec("2026-11-12T01:00:00Z", 48)]);
    expect(rs.map((r) => r.ts)).toEqual(["2026-11-12T01:00:00Z", "2026-11-12T02:00:00Z"]);
    expect(hottestSeries(rs)).toEqual([48, 50]);
  });

  it("shows the latest values with their window", () => {
    const rows = Object.fromEntries(latestRows(rec("t", 52, { throttled: true, services: { "wire-pod": "active" } })));
    expect(rows["Temperature, cpu"]).toBe("52 °C (window 40–52 °C)");
    expect(rows["Memory used"]).toBe("3800 MiB (window 3000–3900 MiB) of 7619 MiB");
    expect(rows["Over-current counters"]).toBe("oc3 7");
    expect(rows["Throttled in this window"]).toBe("yes");
    expect(rows["Service wire-pod"]).toBe("active");
  });

  it("renders nothing without reports, and labels itself a self-report", () => {
    const { container } = render(React.createElement(HealthView, { reports: [] }));
    expect(container.textContent).toBe("");
    render(React.createElement(HealthView, { reports: healthReports([rec("2026-11-12T01:00:00Z", 48), rec("2026-11-12T02:00:00Z", 50)]) }));
    expect(screen.getByText("Machine health")).toBeTruthy();
    expect(screen.getByText(/self-report/)).toBeTruthy();
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/last 50 °C/);
  });
});
