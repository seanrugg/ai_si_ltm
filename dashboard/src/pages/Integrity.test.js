import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Integrity from "./Integrity.jsx";

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
