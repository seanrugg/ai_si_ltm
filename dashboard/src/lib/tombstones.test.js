import { describe, expect, it } from "vitest";
import { missingSeqs, splitRef, tombstoneText } from "./tombstones.js";

describe("splitRef", () => {
  it("splits on the last colon", () => {
    expect(splitRef("z3-r0:41")).toEqual({ agent: "z3-r0", seq: "41" });
    expect(splitRef("hub.local:7")).toEqual({ agent: "hub.local", seq: "7" });
  });
});

describe("tombstoneText", () => {
  const t = { deleted_by: "admin:sean", deleted_at: "2026-11-10T12:00:00+00:00", reason: "test data" };
  it("names the admin without the prefix, and the reason", () => {
    expect(tombstoneText(t)).toMatch(/^Deleted by sean on .+: test data$/);
  });
  it("ends plainly when there is no reason", () => {
    expect(tombstoneText({ ...t, reason: null })).toMatch(/^Deleted by sean on .+\.$/);
  });
});

describe("missingSeqs", () => {
  const recs = (...seqs) => seqs.map((seq) => ({ seq }));
  it("finds the seqs missing between the lowest and highest, in any order", () => {
    expect(missingSeqs(recs(7, 6, 3, 2))).toEqual([4, 5]);
    expect(missingSeqs(recs(1, 2, 3))).toEqual([]);
    expect(missingSeqs([])).toEqual([]);
  });
  it("gives up when more than the limit are missing", () => {
    expect(missingSeqs(recs(1, 5), 3)).toEqual([2, 3, 4]);
    expect(missingSeqs(recs(1, 6), 3)).toEqual([]);
  });
});
