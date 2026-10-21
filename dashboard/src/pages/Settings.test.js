import { describe, expect, it } from "vitest";
import { formatValue, groupSettings } from "./Settings.jsx";

describe("settings page helpers", () => {
  it("formats values with units", () => {
    expect(formatValue("token_ttl_seconds", 300)).toBe("5 min");
    expect(formatValue("admin_session_ttl_seconds", 1800)).toBe("30 min");
    expect(formatValue("retention_audit_days", 90)).toBe("90 days");
    expect(formatValue("retention_payload_rules", [{ type: "event", days: 90 }, { tag: "telemetry", days: 30 }]))
      .toBe("event: 90 days · any type tagged telemetry: 30 days");
    expect(formatValue("retention_payload_rules", [])).toBe("Never");
    expect(formatValue("ingest_rate", 10)).toBe("10 per second");
    expect(formatValue("enroll_rate_per_ip", 5)).toBe("5 per hour");
    expect(formatValue("cookie_secure", true)).toBe("On");
    expect(formatValue("dashboard_dir", "")).toBe("Not set");
  });
  it("puts every key in exactly one group, unknown keys in Other", () => {
    const settings = { db_host: "x", token_ttl_seconds: 1, max_batch: 1, retention_audit_days: 1, brand_new: 1 };
    const groups = groupSettings(settings);
    const all = groups.flatMap((g) => g.keys);
    expect(all.sort()).toEqual(Object.keys(settings).sort());
    expect(groups.find((g) => g.title === "Other").keys).toEqual(["brand_new"]);
  });
});
