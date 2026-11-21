import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Integrity, { fullRunLine, verifiedLine } from "./Integrity.jsx";

const reports = { members: [
  { member: "z3-r0", member_id: "1", status: "ok", records: 5, payloads_purged: 0, deleted: 2, error: null },
  { member: "vector", member_id: "2", status: "ok", records: 3, payloads_purged: 0, deleted: 0, error: null },
] };
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn(() => ({ data: reports, error: null, loading: false, reload: () => {} })),
}));

describe("Integrity", () => {
  it("shows the deleted count per member, and the chain still reads OK", () => {
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(Integrity)));
    expect(html).toContain("5 records verified, 2 records deleted (tombstones kept)");
    expect(html).toContain("3 records verified<");
    expect(html).toContain("All 2 chains verify.");
  });
});

describe("verifiedLine", () => {
  it("says how far a chain is verified, and how", () => {
    expect(verifiedLine({ verified_through: 12, head_seq: 12, mode: "incremental" }))
      .toBe("verified through #12 (incremental)");
    expect(verifiedLine({ verified_through: 5000, head_seq: 31692, mode: "incremental" }))
      .toBe("verified through #5000 of #31692 (incremental)");
    expect(verifiedLine({ records: 3 })).toBe("");
  });
});

describe("fullRunLine", () => {
  it("covers no run, running, failed and done", () => {
    expect(fullRunLine(null)).toBe("No full check has run yet.");
    expect(fullRunLine({ status: "running", members_done: 1, members_total: 3, records_done: 31692 }))
      .toBe(`Full check running: 1 of 3 members, ${(31692).toLocaleString()} records so far.`);
    expect(fullRunLine({ status: "failed", started_at: null, error: "interrupted" })).toContain("failed");
    const done = fullRunLine({ status: "done", finished_at: null, members_total: 2, records_done: 8, broken: 1 });
    expect(done).toContain("2 members, 8 records, 1 chain broken.");
  });
});
