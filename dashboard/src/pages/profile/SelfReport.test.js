import { describe, expect, it } from "vitest";
import { selfReportNote } from "./Overview.jsx";
import { COUNT_SELF_REPORT, COUNT_SENSE } from "../../lib/help.js";

describe("self-reports are counted apart from sensing (#348)", () => {
  it("notes heartbeats left out of the Sensing tile", () => {
    expect(selfReportNote({ flow: { self_report: 0 } })).toBeNull();
    expect(selfReportNote({ flow: { self_report: 1 } })).toBe("1 self-report (heartbeat, health) not counted here");
    expect(selfReportNote({ flow: { self_report: 1200 } })).toMatch(/^1,200 self-reports/);
  });
  it("help says what is and isn't counted", () => {
    expect(COUNT_SENSE).toMatch(/Heartbeats and health reports are not counted/);
    expect(COUNT_SELF_REPORT).toMatch(/heartbeat/);
  });
});
