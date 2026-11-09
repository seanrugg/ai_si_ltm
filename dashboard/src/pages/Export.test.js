import { describe, expect, it } from "vitest";
import { exportUrl, RECORD_TYPES } from "./Export.jsx";

const params = (url) => Object.fromEntries(new URL(url, "https://x.example").searchParams);

describe("exportUrl", () => {
  it("defaults to JSONL with no filters", () => {
    expect(exportUrl({})).toBe("/api/admin/export?format=jsonl");
  });

  it("omits blank fields, and omits type when all types are checked", () => {
    expect(params(exportUrl({ format: "csv", agent: "", swarm: "", types: RECORD_TYPES }))).toEqual({ format: "csv" });
  });

  it("joins a subset of types with commas", () => {
    expect(params(exportUrl({ types: ["thought", "lesson"] })).type).toBe("thought,lesson");
  });

  it("passes member and swarm, and makes To cover the whole UTC day", () => {
    const p = params(exportUrl({ agent: "z3-r0", swarm: "echo-base", from: "2026-11-01", to: "2026-11-09" }));
    expect(p).toEqual({ format: "jsonl", agent: "z3-r0", swarm: "echo-base",
      since: "2026-11-01T00:00:00Z", until: "2026-11-09T23:59:59.999Z" });
  });
});
