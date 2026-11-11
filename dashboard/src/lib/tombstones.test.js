import { describe, expect, it } from "vitest";
import { mergeTombstones, splitRef, tombstoneRange, tombstoneText } from "./tombstones.js";

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

describe("tombstoneRange", () => {
  const page = (...seqs) => seqs.map((seq) => ({ agent: "z3-r0", seq }));
  const opts = { first: true, last: false, since: "", prevLowest: null };

  it("first page: from its lowest seq, no upper bound", () => {
    expect(tombstoneRange(page(9, 8, 6), "z3-r0", opts)).toEqual({ agent: "z3-r0", seq_from: 6 });
  });
  it("a later page stops below what was already loaded", () => {
    expect(tombstoneRange(page(4, 2), "z3-r0", { ...opts, first: false, prevLowest: 6 }))
      .toEqual({ agent: "z3-r0", seq_from: 2, seq_to: 5 });
  });
  it("the last page has no lower bound, unless a date filter set one", () => {
    expect(tombstoneRange(page(4, 2), "z3-r0", { ...opts, first: false, last: true, prevLowest: 6 }))
      .toEqual({ agent: "z3-r0", seq_to: 5 });
    expect(tombstoneRange(page(4, 2), "z3-r0", { ...opts, first: false, last: true, prevLowest: 6, since: "2026-11-01" }))
      .toEqual({ agent: "z3-r0", seq_from: 2, seq_to: 5 });
  });
  it("ignores other members' records, and an empty later page needs nothing", () => {
    expect(tombstoneRange([{ agent: "dx-1", seq: 1 }, ...page(3)], "z3-r0", opts)).toEqual({ agent: "z3-r0", seq_from: 3 });
    expect(tombstoneRange([], "z3-r0", { ...opts, first: false, prevLowest: 3 })).toBeNull();
  });
  it("a member with no records left still lists its deletions", () => {
    expect(tombstoneRange([], "z3-r0", { ...opts, last: true })).toEqual({ agent: "z3-r0" });
  });
});

describe("mergeTombstones", () => {
  it("keeps one per seq, newest first", () => {
    expect(mergeTombstones([{ seq: 5 }, { seq: 2 }], [{ seq: 2 }, { seq: 7 }]).map((t) => t.seq)).toEqual([7, 5, 2]);
  });
});
