import { describe, expect, it, vi } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Export, { exportUrl, RECORD_TYPES } from "./Export.jsx";

vi.mock("../api/useApi.js", () => ({ useApi: vi.fn(() => ({ data: null, error: null, loading: false })) }));
import { addDays, dayStartUtc, localDate, localDayRange } from "../lib/localDays.js";

const params = (url) => Object.fromEntries(new URL(url, "https://x.example").searchParams);
const NY = "America/New_York";
// 13 Nov 2026, 21:16 ET (02:16 UTC on the 14th): when the evening went missing.
const NOV13_EVENING = Date.UTC(2026, 10, 14, 2, 16);
const without = (p, ...keys) => Object.fromEntries(Object.entries(p).filter(([k]) => !keys.includes(k)));

describe("exportUrl", () => {
  it("defaults to JSONL with no filters", () => {
    expect(without(params(exportUrl({})), "local_date")).toEqual({ format: "jsonl" });
  });

  it("omits blank fields, and omits type when all types are checked", () => {
    expect(without(params(exportUrl({ format: "csv", agent: "", swarm: "", types: RECORD_TYPES })), "local_date"))
      .toEqual({ format: "csv" });
  });

  it("joins a subset of types with commas", () => {
    expect(params(exportUrl({ types: ["thought", "lesson"] })).type).toBe("thought,lesson");
  });

  it("passes member and swarm, and makes To cover the whole day (in UTC here)", () => {
    const p = params(exportUrl({ agent: "z3-r0", swarm: "echo-base", from: "2026-11-01", to: "2026-11-09",
      timeZone: "UTC" }));
    expect(p).toEqual({ format: "jsonl", agent: "z3-r0", swarm: "echo-base",
      since: "2026-11-01T00:00:00.000Z", until: "2026-11-09T23:59:59.999Z", local_date: "2026-11-09" });
  });

  it("uses the admin's own calendar days (#394)", () => {
    const p = params(exportUrl({ agent: "v3-c7r", from: "2026-11-13", to: "2026-11-13", timeZone: NY,
      now: NOV13_EVENING }));
    expect(p.since).toBe("2026-11-13T05:00:00.000Z");
    expect(p.until).toBe("2026-11-14T04:59:59.999Z"); // 23:59:59.999 ET: the evening is in
    expect(p.local_date).toBe("2026-11-13");
    expect(new Date(p.until).getTime()).toBeGreaterThan(NOV13_EVENING);
  });

  it("handles days that change DST", () => {
    // Spring forward: 8 Mar 2026 is 23 hours long in New York.
    expect(localDayRange("2026-03-08", "2026-03-08", NY))
      .toEqual({ since: "2026-03-08T05:00:00.000Z", until: "2026-03-09T03:59:59.999Z" });
    // Fall back: 1 Nov 2026 is 25 hours long.
    expect(localDayRange("2026-11-01", "2026-11-01", NY))
      .toEqual({ since: "2026-11-01T04:00:00.000Z", until: "2026-11-02T04:59:59.999Z" });
    // East of UTC: Tokyo's 14 Nov starts the previous UTC evening.
    expect(localDayRange("2026-11-14", "", "Asia/Tokyo")).toEqual({ since: "2026-11-13T15:00:00.000Z" });
  });

  it("rolling quick ranges end now and ignore the dates", () => {
    const base = { now: NOV13_EVENING, timeZone: NY, from: "2026-01-01", to: "2026-01-02" };
    expect(params(exportUrl({ ...base, range: "hour" }))).toMatchObject({ since: "2026-11-14T01:16:00.000Z" });
    expect(params(exportUrl({ ...base, range: "day" }))).toMatchObject({ since: "2026-11-13T02:16:00.000Z" });
    const week = params(exportUrl({ ...base, range: "week" }));
    expect(week.since).toBe("2026-11-07T02:16:00.000Z");
    expect(week.until).toBeUndefined();
    expect(week.local_date).toBe("2026-11-13"); // today, ET, not the UTC date
  });

  it("names the file after the local date when no To is set", () => {
    expect(params(exportUrl({ agent: "v3-c7r", now: NOV13_EVENING, timeZone: NY })).local_date).toBe("2026-11-13");
    expect(params(exportUrl({ now: NOV13_EVENING, timeZone: "UTC" })).local_date).toBe("2026-11-14");
  });
});

describe("localDays", () => {
  it("dates and day starts", () => {
    expect(localDate(NOV13_EVENING, NY)).toBe("2026-11-13");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(new Date(dayStartUtc("2026-07-04", NY)).toISOString()).toBe("2026-07-04T04:00:00.000Z");
  });
});

describe("Export page", () => {
  it("says which zone the dates are in and offers the quick ranges", () => {
    const html = renderToStaticMarkup(React.createElement(Export));
    expect(html).toMatch(/Dates are in your time zone \([^)]+\)\. To includes the whole day\./);
    for (const label of ["Last hour", "Last 24 hours", "Today", "Last 7 days", "All"]) expect(html).toContain(`>${label}<`);
    expect(html).toContain("local_date=");
  });
});
